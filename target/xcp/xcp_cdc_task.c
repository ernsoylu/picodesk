/* XCP slave task: TinyUSB CDC-ACM transport + coherent DAQ drain (CAL-001).
 *
 * Runs on core 1 at the LOWEST application priority (BLD-005). tusb_init()
 * is called from this task, so the USB IRQ lands on core 1's NVIC and never
 * perturbs the core 0 fast path (NFR-1 risk mitigation).
 *
 * DAQ path (RTE-005): the fast ISR pushes whole frames into the SRAM2 ring;
 * this task pops them and lets the protocol core cut configured ODT entries
 * out of each coherent frame — the slave never samples live memory for DAQ.
 */

#include "FreeRTOS.h"
#include "task.h"
#include "tusb.h"

#include "rte.h"
#include "rte_sections.h"
#include "xcp_core.h"
#include "xcp_transport.h"

/* The logical DAQ frame address the A2L/master uses for fast-loop signals.
 * Any address is workable as long as master and slave agree; the canonical
 * frame lives at this SRAM2-external marker so it cannot collide with real
 * symbols. Phase 4 emits it into the generated A2L. */
uint8_t g_xcp_daq_frame_marker[sizeof(spike_daq_frame_t)] RTE_SHARED;

static xcp_slave_t s_slave RTE_CORE1_BSS;
static xcp_tl_assembler_t s_asm RTE_CORE1_BSS;
static uint16_t s_tx_ctr RTE_CORE1_BSS;

static void cdc_send_framed(const uint8_t *pkt, uint8_t len) {
    uint8_t hdr[XCP_TL_HEADER_LEN];
    hdr[0] = len;
    hdr[1] = 0;
    hdr[2] = (uint8_t) (s_tx_ctr & 0xFF);
    hdr[3] = (uint8_t) (s_tx_ctr >> 8);
    s_tx_ctr++;
    tud_cdc_write(hdr, sizeof hdr);
    tud_cdc_write(pkt, len);
}

/* --- xcp_core hooks ------------------------------------------------------ */

static uint8_t *hook_cal_offline(void *user) {
    (void) user;
    return (uint8_t *) rte_cal_offline();
}

static void hook_cal_request_switch(void *user) {
    (void) user;
    rte_cal_request_switch();
}

static uint8_t hook_cal_active_page(void *user) {
    (void) user;
    return (uint8_t) rte_cal_active_index();
}

static bool hook_mem_ok(void *user, uint32_t addr, uint32_t len, bool write) {
    (void) user;
    /* Reads: any SRAM bank alias or XIP flash. Writes outside the CAL window
     * are denied — calibration goes through pages only (RTE-003). */
    if (write) {
        return false;
    }
    const uint32_t end = addr + len;
    if (addr >= 0x21000000u && end <= 0x21040000u) {
        return true; /* SRAM0-3 bank aliases */
    }
    if (addr >= 0x20000000u && end <= 0x20042000u) {
        return true; /* striped alias + scratch */
    }
    if (addr >= 0x10000000u && end <= 0x10200000u) {
        return true; /* XIP flash */
    }
    return false;
}

static void hook_dto_send(void *user, const uint8_t *pkt, uint8_t len) {
    (void) user;
    cdc_send_framed(pkt, len);
}

/* --- task ---------------------------------------------------------------- */

void xcp_task(void *arg) {
    (void) arg;

    s_slave.cal_logical_base = (uint8_t *) rte_cal_logical_base();
    s_slave.cal_size = sizeof(spike_cal_t);
    s_slave.cal_offline = hook_cal_offline;
    s_slave.cal_request_switch = hook_cal_request_switch;
    s_slave.cal_active_page = hook_cal_active_page;
    s_slave.mem_ok = hook_mem_ok;
    s_slave.dto_send = hook_dto_send;
    s_slave.user = NULL;
    xcp_init(&s_slave);

    tusb_init(); /* USB IRQ binds to this core (core 1) */

    spike_daq_frame_t frame;
    uint8_t resp[XCP_MAX_CTO];

    for (;;) {
        tud_task();

        /* Command channel. */
        while (tud_cdc_available()) {
            uint8_t byte;
            if (tud_cdc_read(&byte, 1) != 1) {
                break;
            }
            const uint16_t plen = xcp_tl_feed(&s_asm, byte);
            if (plen > 0) {
                const uint8_t rlen = xcp_command(
                    &s_slave, s_asm.buf + XCP_TL_HEADER_LEN, (uint8_t) plen, resp);
                cdc_send_framed(resp, rlen);
            }
        }

        /* Coherent DAQ drain (RTE-005). */
        while (rte_daq_pop_frame(&frame)) {
            if (s_slave.daq_running && tud_cdc_connected()) {
                xcp_daq_event0(&s_slave, (const uint8_t *) &frame, sizeof frame,
                               (uint32_t) (uintptr_t) g_xcp_daq_frame_marker);
            }
        }

        tud_cdc_write_flush();
        vTaskDelay(1);
    }
}
