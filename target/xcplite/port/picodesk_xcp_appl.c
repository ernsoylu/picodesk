/* XCPlite application callbacks, bound to the PicoDesk RTE.
 *
 * The decision this file encodes (docs/XCPLITE_FEASIBILITY.md): the RTE keeps
 * ownership of the calibration page swap, and XCPlite's SET_CAL_PAGE merely
 * *arms* one. That preserves RTE-003 exactly — a multi-parameter change
 * becomes visible to the fast loop atomically at a model_step() boundary,
 * because core 0 flips the pointer itself at a point it chooses. Adopting
 * XCPlite's own lock-less calibration instead would give memory safety but
 * not the step-boundary guarantee, and would mean reopening the requirement.
 *
 * Behaviour here is deliberately identical to the interim core in
 * target/xcp/xcp_core.c, so substituting the library changes no wire
 * behaviour the host tooling depends on.
 */

#include "main.h"
#include "xcpLite.h"

#include "rte.h"
#include "rte_sections.h"

/* Where the master's DAQ frame reads land (RTE-005). Defined by the CDC task
 * on hardware builds; declared here so the address guard can recognise it. */
extern uint8_t g_xcp_daq_frame_marker[];

/* --- calibration pages (RTE-003) ----------------------------------------- */

uint8_t ApplXcpGetCalPage(uint8_t segment, uint8_t mode) {
    (void) mode; /* one logical segment; ECU and XCP access track together */
    if (segment != 0) {
        return 0;
    }
    return (uint8_t) rte_cal_active_index();
}

uint8_t ApplXcpSetCalPage(uint8_t segment, uint8_t page, uint8_t mode) {
    (void) mode;
    if (segment != 0 || page > 1) {
        return CRC_OUT_OF_RANGE;
    }
    /* Arm only. Core 0 commits at the next model_step() boundary, which is
     * what makes the multi-parameter change transactional. */
    if (page != (uint8_t) rte_cal_active_index()) {
        rte_cal_request_switch();
    }
    return 0;
}

/* --- addressing ----------------------------------------------------------- */

/* XCP addresses are the target's own pointers, so the base address is zero
 * and the mapping is the identity. That keeps the DWARF-derived A2L addresses
 * (CAL-002) directly usable by the master with no translation table to drift
 * out of date. */

uint8_t *ApplXcpGetBaseAddr(void) { return (uint8_t *) 0; }

uint32_t ApplXcpGetAddr(uint8_t *p) { return (uint32_t) (uintptr_t) p; }

uint8_t *ApplXcpGetPointer(uint8_t xcpAddrExt, uint32_t xcpAddr) {
    if (xcpAddrExt != 0) {
        return NULL; /* extensions 0x01/0xFF are not enabled in xcp_cfg.h */
    }
    /* CAL window redirection: the A2L names one logical calibration window,
     * and every access inside it is redirected to the OFFLINE page, so edits
     * stay invisible to the fast loop until SET_CAL_PAGE arms the switch. */
    const uint32_t cal_base = (uint32_t) (uintptr_t) rte_cal_logical_base();
    if (xcpAddr >= cal_base && xcpAddr < cal_base + sizeof(spike_cal_t)) {
        return (uint8_t *) rte_cal_offline() + (xcpAddr - cal_base);
    }
    return (uint8_t *) (uintptr_t) xcpAddr;
}

/* --- session -------------------------------------------------------------- */

BOOL ApplXcpConnect(void) {
    return TRUE; /* XCP Seed/Key is out of scope for v1.0 (SRS 1.3) */
}

BOOL ApplXcpPrepareDaq(void) { return TRUE; }

BOOL ApplXcpStartDaq(void) { return TRUE; }

void ApplXcpStopDaq(void) {}

/* --- clock ---------------------------------------------------------------- */

uint64_t ApplXcpGetClock64(void) { return clockGet(); }

uint8_t ApplXcpGetClockState(void) { return CLOCK_STATE_FREE_RUNNING; }

/* --- identification ------------------------------------------------------- */

uint32_t ApplXcpGetId(uint8_t id, uint8_t *buf, uint32_t bufLen) {
    /* The host already holds the A2L it generated from this firmware's DWARF,
     * so only the identifying strings are answered here. */
    const char *s = NULL;
    switch (id) {
    case IDT_ASCII:
    case IDT_ASAM_NAME:
        s = "PicoDesk";
        break;
    case IDT_ASAM_PATH:
        s = "PicoDesk.a2l";
        break;
    default:
        return 0;
    }
    const uint32_t n = (uint32_t) strlen(s);
    if (buf != NULL) {
        if (n > bufLen) {
            return 0;
        }
        memcpy(buf, s, n);
    }
    return n;
}
