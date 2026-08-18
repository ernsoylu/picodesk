/* XCPlite transport layer over USB CDC-ACM (CAL-001).
 *
 * Implements the contract in xcpTl.h. The wire format is unchanged from the
 * interim core this replaces:
 *
 *     uint16 LEN (LE) | uint16 CTR | packet
 *
 * which is both XCPlite's Ethernet TL header and pyxcp's SxI
 * HEADER_LEN_CTR_WORD framing — see xcptl_cfg.h for why the transport type
 * is nominally ETH. Receive framing reuses xcp_tl_feed() from the interim
 * transport header rather than restating it, so there is one definition of
 * the wire format in the tree and the host tooling needs no change.
 *
 * Concurrency:
 *  - Everything here runs on the core 1 XCP task. The core 0 fast path never
 *    calls into this file; its only contact with XCP is pushing coherent
 *    frames into the DAQ ring (RTE-005), which core 1 drains.
 *  - The queue lives in SRAM2 with the rest of the shared RTE data
 *    (BLD-002), away from the core 0 bank.
 *
 * PICODESK_SIM builds compile the whole layer but stub the USB calls,
 * because Renode does not model the RP2040 USB block.
 */

#include <string.h>

#include "main.h"
#include "xcpLite.h"

#include "rte_sections.h"
#include "xcp_transport.h"

#ifdef PICODESK_NATIVE
/* tests/native links this file against the real protocol core with no RTOS
 * and no USB; the waits below become no-ops because the test drives the
 * queue synchronously through the seam at the bottom of this file. */
#define TL_DELAY_MS(ms) ((void) (ms))
#else
#include "FreeRTOS.h"
#include "task.h"
#define TL_DELAY_MS(ms) vTaskDelay(pdMS_TO_TICKS(ms))
#ifndef PICODESK_SIM
#include "tusb.h"
#endif
#endif

/* Queue depth in bytes. Sized for the DAQ burst produced between two drains:
 * >= 50 signals at 100 Hz (CAL-001) against a 1 kHz drain is a few hundred
 * bytes, so 2 KB is ~4x headroom while staying affordable in SRAM2. */
#define TL_QUEUE_BYTES 2048u

typedef struct {
    uint8_t buf[TL_QUEUE_BYTES];
    uint32_t head; /* bytes produced */
    uint32_t tail; /* bytes drained  */
    uint16_t tx_ctr;
    int32_t last_error;

    /* Alignment here is load-bearing on Cortex-M0+, which has no unaligned
     * access — a misaligned word touch is a HardFault, not a slow path.
     *
     * Receive: the core reads commands through a uint32_t* (CRO_DWORD(n)).
     * rx.buf sits at offset 0 of the assembler and the packet begins 4 bytes
     * in, so aligning the struct aligns the packet.
     *
     * Transmit: the core writes the DAQ timestamp as
     * `*((uint32_t*)&d0[2]) = clock` — a word store at offset 2 of the buffer
     * this layer hands back. So the returned pointer must be congruent to 2
     * mod 4, not 4-aligned. TX_STAGING_SKEW below is what produces that; the
     * two bytes it wastes are the price of leaving the vendored source
     * byte-identical to upstream. Upstream never hits this because its own
     * transports run on architectures that fix up unaligned accesses. */
#define TX_STAGING_SKEW 2u
    uint8_t staging[TX_STAGING_SKEW + XCPTL_MAX_DTO_SIZE] __attribute__((aligned(4)));
    uint16_t staging_len;
    xcp_tl_assembler_t rx __attribute__((aligned(4)));
} tl_state_t;

static tl_state_t s_tl RTE_SHARED;

/* --- queue helpers ------------------------------------------------------- */

static uint32_t tl_free(void) { return TL_QUEUE_BYTES - (s_tl.head - s_tl.tail); }

static void tl_push(const uint8_t *data, uint16_t len) {
    for (uint16_t i = 0; i < len; i++) {
        s_tl.buf[(s_tl.head + i) % TL_QUEUE_BYTES] = data[i];
    }
    s_tl.head += len;
}

/* Frame and enqueue one XCP packet. A full queue drops the packet rather
 * than blocking: stalling here would back-pressure into the protocol core
 * and, through it, into the DAQ path. XCP tolerates a lost DTO (the master
 * sees the counter gap); a stalled control loop is not tolerable. */
static void tl_frame(const uint8_t *packet, uint16_t len) {
    const uint16_t total = (uint16_t) (XCP_TL_HEADER_LEN + len);
    if (tl_free() < total) {
        s_tl.last_error = XCPTL_ERROR_WOULD_BLOCK;
        return;
    }
    const uint8_t header[XCP_TL_HEADER_LEN] = {
        (uint8_t) (len & 0xFF),
        (uint8_t) (len >> 8),
        (uint8_t) (s_tl.tx_ctr & 0xFF),
        (uint8_t) (s_tl.tx_ctr >> 8),
    };
    s_tl.tx_ctr++;
    tl_push(header, XCP_TL_HEADER_LEN);
    tl_push(packet, len);
}

/* --- xcpTl.h contract ---------------------------------------------------- */

void XcpTlSendCrm(const uint8_t *data, uint16_t n) { tl_frame(data, n); }

uint8_t *XcpTlGetTransmitBuffer(void **par, uint16_t size) {
    if (size > XCPTL_MAX_DTO_SIZE) {
        s_tl.last_error = XCPTL_ERROR_SEND_FAILED;
        *par = NULL;
        return NULL;
    }
    /* One staging buffer suffices: the protocol core commits each buffer
     * before requesting the next, and only the XCP task calls this. */
    s_tl.staging_len = size;
    *par = &s_tl;
    return s_tl.staging + TX_STAGING_SKEW; /* see the note on TX_STAGING_SKEW */
}

void XcpTlCommitTransmitBuffer(void *par, BOOL flush) {
    (void) flush;
    if (par == NULL) {
        return;
    }
    tl_frame(s_tl.staging + TX_STAGING_SKEW, s_tl.staging_len);
    s_tl.staging_len = 0;
}

void XcpTlFlushTransmitBuffer(void) {
    /* Nothing to coalesce: each packet is framed on commit and one packet
     * fits one CDC bulk transfer. */
}

void XcpTlWaitForTransmitQueueEmpty(void) {
    for (uint32_t guard = 0; guard < 1000u && s_tl.head != s_tl.tail; guard++) {
        XcpTlHandleTransmitQueue();
        TL_DELAY_MS(1);
    }
}

int32_t XcpTlGetTransmitQueueLevel(void) { return (int32_t) (s_tl.head - s_tl.tail); }

int32_t XcpTlGetLastError(void) {
    const int32_t error = s_tl.last_error;
    s_tl.last_error = XCPTL_OK;
    return error;
}

BOOL XcpTlWaitForTransmitData(uint32_t timeout_ms) {
    for (uint32_t waited = 0; waited < timeout_ms && s_tl.head == s_tl.tail; waited++) {
        TL_DELAY_MS(1);
    }
    return s_tl.head != s_tl.tail ? TRUE : FALSE;
}

void XcpTlShutdown(void) {
    s_tl.head = 0;
    s_tl.tail = 0;
    s_tl.rx.have = 0;
}

/* --- Ethernet-TL entry points referenced by the protocol core ------------- */

/* Selecting XCP_TRANSPORT_LAYER_ETH for the framing (xcptl_cfg.h) also makes
 * the core reference these two. Neither has a meaning on a point-to-point
 * CDC link: there is no multicast group and no IP address to report. */

void XcpEthTlSendMulticastCrm(const uint8_t *data, uint16_t n, const uint8_t *addr,
                              uint16_t port) {
    (void) addr;
    (void) port;
    tl_frame(data, n); /* single master: unicast is the only destination */
}

void XcpEthTlGetInfo(BOOL *isTCP, uint8_t *mac, uint8_t *addr, uint16_t *port) {
    if (isTCP != NULL) {
        *isTCP = FALSE;
    }
    if (mac != NULL) {
        memset(mac, 0, 6);
    }
    if (addr != NULL) {
        memset(addr, 0, 4);
    }
    if (port != NULL) {
        *port = 0;
    }
}

/* --- USB plumbing -------------------------------------------------------- */

int32_t XcpTlHandleTransmitQueue(void) {
    int32_t sent = 0;
#if defined(PICODESK_NATIVE)
    /* The test drains through picodesk_xcp_tl_take_tx() instead, so that it
     * can assert on the exact bytes that would have gone down the wire. */
    sent = (int32_t) (s_tl.head - s_tl.tail);
#elif !defined(PICODESK_SIM)
    while (s_tl.head != s_tl.tail) {
        const uint32_t offset = s_tl.tail % TL_QUEUE_BYTES;
        uint32_t chunk = s_tl.head - s_tl.tail;
        if (chunk > TL_QUEUE_BYTES - offset) {
            chunk = TL_QUEUE_BYTES - offset; /* stop at the wrap */
        }
        const uint32_t room = tud_cdc_write_available();
        if (room == 0) {
            break;
        }
        if (chunk > room) {
            chunk = room;
        }
        const uint32_t wrote = tud_cdc_write(&s_tl.buf[offset], chunk);
        if (wrote == 0) {
            break;
        }
        s_tl.tail += wrote;
        sent += (int32_t) wrote;
    }
    tud_cdc_write_flush();
#else
    /* Renode does not model the USB block: drain so the queue cannot wedge
     * and the rest of the stack still runs. */
    sent = (int32_t) (s_tl.head - s_tl.tail);
    s_tl.tail = s_tl.head;
#endif
    return sent;
}

BOOL XcpTlHandleCommands(uint32_t timeout_ms) {
    (void) timeout_ms;
#if !defined(PICODESK_SIM) && !defined(PICODESK_NATIVE)
    while (tud_cdc_available()) {
        uint8_t byte;
        if (tud_cdc_read(&byte, 1) != 1) {
            break;
        }
        const uint16_t len = xcp_tl_feed(&s_tl.rx, byte);
        if (len > 0) {
            XcpCommand((const uint32_t *) (void *) (s_tl.rx.buf + XCP_TL_HEADER_LEN),
                       (uint8_t) len);
        }
    }
#endif
    return TRUE;
}

/* Test seam: lets tests/native drive the protocol core with real XCP frames
 * without a USB stack, and inspect what the target framed in reply. */
uint16_t picodesk_xcp_tl_feed_rx(const uint8_t *bytes, uint16_t n) {
    uint16_t handled = 0;
    for (uint16_t i = 0; i < n; i++) {
        const uint16_t len = xcp_tl_feed(&s_tl.rx, bytes[i]);
        if (len > 0) {
            XcpCommand((const uint32_t *) (void *) (s_tl.rx.buf + XCP_TL_HEADER_LEN),
                       (uint8_t) len);
            handled++;
        }
    }
    return handled;
}

uint16_t picodesk_xcp_tl_take_tx(uint8_t *out, uint16_t max) {
    uint16_t n = 0;
    while (s_tl.head != s_tl.tail && n < max) {
        out[n++] = s_tl.buf[s_tl.tail % TL_QUEUE_BYTES];
        s_tl.tail++;
    }
    return n;
}

void picodesk_xcp_tl_reset(void) {
    memset(&s_tl, 0, sizeof s_tl);
}
