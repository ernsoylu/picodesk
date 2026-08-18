/* XCP slave task driving vendored XCPlite over CDC-ACM (CAL-001).
 *
 * Structural replacement for target/xcp/xcp_cdc_task.c: same core, same
 * priority, same DAQ drain, with the protocol engine swapped for the real
 * library. Runs on core 1 at the LOWEST application priority (BLD-005), and
 * tusb_init() is called from here so the USB IRQ lands on core 1's NVIC and
 * never perturbs the core 0 fast path (NFR-1).
 *
 * DAQ path (RTE-005): the fast ISR pushes whole coherent frames into the
 * SRAM2 ring; this task pops them and rebases the XCP event onto the popped
 * copy, so the slave never samples live memory for DAQ. See the rebase note
 * at the XcpEventExt() call.
 */

#include "FreeRTOS.h"
#include "task.h"

#include "main.h"
#include "xcpLite.h"

#include "rte.h"
#include "rte_sections.h"

#ifndef PICODESK_SIM
#include "tusb.h"
#endif

/* The logical address the A2L and master use for fast-loop DAQ signals. Any
 * address works as long as both agree; anchoring it to a real object keeps it
 * from colliding with other symbols and makes the rebase arithmetic exact. */
uint8_t g_xcp_daq_frame_marker[sizeof(spike_daq_frame_t)] RTE_SHARED;

/* Fast rate group event, published to the master through the event list. */
static uint16_t s_event_fast RTE_CORE1_BSS;

uint16_t picodesk_xcp_fast_event(void) { return s_event_fast; }

void xcp_task(void *arg) {
    (void) arg;

    XcpInit();
    XcpClearEventList();
    s_event_fast = XcpCreateEvent("fast_1ms", 1000000u /* ns */, 1 /* realtime */,
                                  0, sizeof(spike_daq_frame_t));
    XcpStart();

#ifndef PICODESK_SIM
    tusb_init(); /* USB IRQ binds to this core (core 1) */
#endif

    spike_daq_frame_t frame;

    for (;;) {
#ifndef PICODESK_SIM
        tud_task();
#endif
        XcpTlHandleCommands(0);

        /* Coherent DAQ drain (RTE-005). The master's ODT entries address the
         * frame marker; passing base = &frame - marker makes the library read
         * every entry out of this popped copy instead of live memory, so a
         * frame can never tear across the fast loop's next step. */
        while (rte_daq_pop_frame(&frame)) {
            if (XcpIsDaqRunning()) {
                uint8_t *const base = (uint8_t *) ((uintptr_t) &frame -
                                                   (uintptr_t) g_xcp_daq_frame_marker);
                XcpEventExt(s_event_fast, base);
            }
        }

        XcpTlHandleTransmitQueue();
        vTaskDelay(1);
    }
}
