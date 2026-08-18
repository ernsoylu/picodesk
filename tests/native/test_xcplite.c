/* Vendored Vector XCPlite driven through the PicoDesk port (CAL-001).
 *
 * This is the substitution proof: the same master-side sequence that
 * test_xcp_core.c runs against the interim core is run against the real
 * library plus target/xcplite/port, over the same CDC framing. What it has to
 * show is that the library does not quietly take calibration away from the
 * RTE — writes must land on the OFFLINE page and stay invisible until core 0
 * commits the switch at a step boundary (RTE-003) — and that DAQ is cut from
 * coherent popped frames, never sampled live (RTE-005).
 *
 * Frames go in as bytes through the transport layer, so the transport framing
 * is under test too, not bypassed.
 */

#include <stdint.h>
#include <string.h>

#include "main.h"
#include "xcpLite.h"

#include "mini_test.h"
#include "rte_calpage.h"
#include "spike_bus.h"

/* --- RTE stand-in --------------------------------------------------------
 * rte_cal_* live in rte_dispatch.c, which is hardware-bound. The page
 * mechanism itself is the real rte_calpage.c, so what the callbacks talk to
 * here behaves exactly as it does on target. */

static rte_calpage_t cp;
static spike_cal_t page_a, page_b;

spike_cal_t *rte_cal_offline(void) { return (spike_cal_t *) rte_calpage_offline(&cp); }
void rte_cal_request_switch(void) { rte_calpage_request_switch(&cp); }
uint32_t rte_cal_active_index(void) { return cp.active_idx & 1u; }
void *rte_cal_logical_base(void) { return &page_a; }

/* Defined by picodesk_xcp_task.c on target; that file is RTOS-bound, so the
 * DAQ frame marker is provided here. */
uint8_t g_xcp_daq_frame_marker[sizeof(spike_daq_frame_t)];

/* --- transport seam ------------------------------------------------------ */

uint16_t picodesk_xcp_tl_feed_rx(const uint8_t *bytes, uint16_t n);
uint16_t picodesk_xcp_tl_take_tx(uint8_t *out, uint16_t max);
void picodesk_xcp_tl_reset(void);

static uint8_t tx[512];
static uint16_t tx_len;

/* Frame a command the way the master does and push it through the transport:
 *   uint16 LEN | uint16 CTR | packet
 * Returns the response packet length, with the packet in `resp`. */
static uint16_t command(const uint8_t *packet, uint16_t len, uint8_t *resp) {
    uint8_t framed[4 + 64];
    framed[0] = (uint8_t) (len & 0xFF);
    framed[1] = (uint8_t) (len >> 8);
    framed[2] = 0;
    framed[3] = 0;
    memcpy(framed + 4, packet, len);

    picodesk_xcp_tl_take_tx(tx, sizeof tx); /* discard anything pending */
    picodesk_xcp_tl_feed_rx(framed, (uint16_t) (4 + len));
    tx_len = picodesk_xcp_tl_take_tx(tx, sizeof tx);
    if (tx_len < 4) {
        return 0;
    }
    const uint16_t n = (uint16_t) (tx[0] | (tx[1] << 8));
    memcpy(resp, tx + 4, n);
    return n;
}

static void wr32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t) v;
    p[1] = (uint8_t) (v >> 8);
    p[2] = (uint8_t) (v >> 16);
    p[3] = (uint8_t) (v >> 24);
}

static void wr16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t) v;
    p[1] = (uint8_t) (v >> 8);
}

int main(void) {
    const spike_cal_t defaults = SPIKE_CAL_DEFAULTS;
    rte_calpage_init(&cp, &page_a, &page_b, sizeof(spike_cal_t), &defaults);
    picodesk_xcp_tl_reset();

    XcpInit();
    XcpClearEventList();
    const uint16_t ev = XcpCreateEvent("fast_1ms", 1000000u, 1, 0,
                                       sizeof(spike_daq_frame_t));
    MT_CHECK(ev != XCP_INVALID_EVENT);
    XcpStart();

    uint8_t resp[64];

    /* CONNECT. The library must advertise the CDC-sized CTO/DTO from
     * xcptl_cfg.h, not upstream's Ethernet defaults — if it reports 252/1472
     * the master will send frames the CDC assembler drops. */
    MT_CHECK_EQ(command((const uint8_t[]) {CC_CONNECT, 0x00}, 2, resp), 8);
    MT_CHECK_EQ(resp[0], 0xFF);
    MT_CHECK_EQ(resp[3], XCPTL_MAX_CTO_SIZE);
    MT_CHECK_EQ((uint16_t) (resp[4] | (resp[5] << 8)), XCPTL_MAX_DTO_SIZE);
    MT_CHECK(XcpIsConnected());

    /* --- RTE-003: calibration stays transactional -------------------------
     * SET_MTA into the logical CAL window, then DOWNLOAD. The write must land
     * on the offline page; the active page the fast loop reads must not move. */
    uint8_t set_mta[8] = {CC_SET_MTA, 0, 0, 0};
    wr32(&set_mta[4], (uint32_t) (uintptr_t) &page_a); /* kp_q15, offset 0 */
    MT_CHECK_EQ(command(set_mta, 8, resp), 1);

    uint8_t dl[6] = {CC_DOWNLOAD, 4, 0, 0, 0, 0};
    wr32(&dl[2], 11469);
    MT_CHECK_EQ(command(dl, 6, resp), 1);
    MT_CHECK_EQ(((spike_cal_t *) rte_calpage_offline(&cp))->kp_q15, 11469);
    MT_CHECK_EQ(((const spike_cal_t *) rte_calpage_active(&cp))->kp_q15, 9830);

    /* Verify-after-write reads back through the same redirection. */
    uint8_t su[8] = {CC_SHORT_UPLOAD, 4, 0, 0};
    wr32(&su[4], (uint32_t) (uintptr_t) &page_a);
    MT_CHECK_EQ(command(su, 8, resp), 5);
    MT_CHECK_EQ((int32_t) (resp[1] | (resp[2] << 8) | (resp[3] << 16) |
                           ((uint32_t) resp[4] << 24)),
                11469);

    /* SET_CAL_PAGE only ARMS the switch. Nothing may become visible to the
     * fast loop until core 0 commits at its model_step() boundary — this is
     * the guarantee that would have been lost by adopting XCPlite's own
     * lock-less calibration instead. */
    MT_CHECK_EQ(command((const uint8_t[]) {CC_SET_CAL_PAGE, 0x03, 0, 1}, 4, resp), 1);
    MT_CHECK_EQ(cp.switch_request, 1u);
    MT_CHECK_EQ(((const spike_cal_t *) rte_calpage_active(&cp))->kp_q15, 9830);

    MT_CHECK(rte_calpage_commit(&cp)); /* core 0, step boundary */
    MT_CHECK_EQ(((const spike_cal_t *) rte_calpage_active(&cp))->kp_q15, 11469);

    MT_CHECK_EQ(command((const uint8_t[]) {CC_GET_CAL_PAGE, 0x03, 0}, 3, resp), 4);
    MT_CHECK_EQ(resp[3], 1);

    /* --- RTE-005: DAQ is cut from coherent frames -------------------------
     * One list, one ODT, two entries addressing the DAQ frame marker. */
    const uint32_t marker = (uint32_t) (uintptr_t) g_xcp_daq_frame_marker;

    MT_CHECK_EQ(command((const uint8_t[]) {CC_FREE_DAQ}, 1, resp), 1);

    uint8_t alloc_daq[4] = {CC_ALLOC_DAQ, 0, 0, 0};
    wr16(&alloc_daq[2], 1);
    MT_CHECK_EQ(command(alloc_daq, 4, resp), 1);

    uint8_t alloc_odt[5] = {CC_ALLOC_ODT, 0, 0, 0, 1};
    wr16(&alloc_odt[2], 0);
    MT_CHECK_EQ(command(alloc_odt, 5, resp), 1);

    uint8_t alloc_ent[6] = {CC_ALLOC_ODT_ENTRY, 0, 0, 0, 0, 2};
    wr16(&alloc_ent[2], 0);
    MT_CHECK_EQ(command(alloc_ent, 6, resp), 1);

    uint8_t set_ptr[6] = {CC_SET_DAQ_PTR, 0, 0, 0, 0, 0};
    MT_CHECK_EQ(command(set_ptr, 6, resp), 1);

    uint8_t wd[8] = {CC_WRITE_DAQ, 0, 4, 0};
    wr32(&wd[4], marker + offsetof(spike_daq_frame_t, torque_cmd));
    MT_CHECK_EQ(command(wd, 8, resp), 1);
    wr32(&wd[4], marker + offsetof(spike_daq_frame_t, speed_est));
    MT_CHECK_EQ(command(wd, 8, resp), 1);

    /* DAQ_MODE_TIMESTAMP is mandatory here — XCPlite rejects a list without
     * it (CRC_CMD_SYNTAX), where the interim core accepted mode 0. pyxcp sets
     * the bit by default, so this is a difference in strictness rather than in
     * what a real master sends, but it is a difference. */
    uint8_t mode[8] = {CC_SET_DAQ_LIST_MODE, DAQ_MODE_TIMESTAMP, 0, 0, 0, 0, 1, 0};
    wr16(&mode[2], 0);   /* daq list 0   */
    wr16(&mode[4], ev);  /* event        */
    MT_CHECK_EQ(command(mode, 8, resp), 1);

    uint8_t start[4] = {CC_START_STOP_DAQ_LIST, 1, 0, 0};
    MT_CHECK(command(start, 4, resp) >= 1);

    MT_CHECK(XcpIsDaqRunning());
    picodesk_xcp_tl_take_tx(tx, sizeof tx);

    /* Fire the event rebased onto a popped frame, exactly as the XCP task
     * does. The library must read the two entries out of this copy — if it
     * sampled live memory it would read the zeroed marker instead. */
    const spike_daq_frame_t frame = {.tick = 7,
                                     .torque_cmd = -1234,
                                     .iq_meas = 55,
                                     .speed_est = 4321};
    /* ODT entries hold 32-bit addresses, so the rebase must subtract the same
     * truncated value the master sent. On the 32-bit target this is just the
     * pointer; here the truncation has to be explicit or base+addr lands
     * nowhere near the frame. */
    uint8_t *const base = (uint8_t *) ((uintptr_t) &frame - (uintptr_t) marker);
    XcpEventExt(ev, base);

    tx_len = picodesk_xcp_tl_take_tx(tx, sizeof tx);
    MT_CHECK(tx_len > 4);
    const uint16_t dto_len = (uint16_t) (tx[0] | (tx[1] << 8));
    const uint8_t *dto = tx + 4;

    /* Locate the two payload words: the ODT header length varies with the
     * timestamp configuration, so search rather than hard-code an offset. */
    int found_torque = 0, found_speed = 0;
    for (uint16_t i = 0; i + 4 <= dto_len; i++) {
        int32_t v;
        memcpy(&v, dto + i, 4);
        if (v == -1234) {
            found_torque = 1;
        }
        if (v == 4321) {
            found_speed = 1;
        }
    }
    MT_CHECK(found_torque);
    MT_CHECK(found_speed);

    /* STOP and DISCONNECT. */
    uint8_t stop[4] = {CC_START_STOP_DAQ_LIST, 0, 0, 0};
    MT_CHECK(command(stop, 4, resp) >= 1);
    MT_CHECK_EQ(command((const uint8_t[]) {CC_DISCONNECT}, 1, resp), 1);
    MT_CHECK(!XcpIsConnected());

    return mt_summary("xcplite");
}
