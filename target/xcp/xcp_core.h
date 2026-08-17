/* Minimal XCP 1.x slave core (CAL-001 interim).
 *
 * Portable, dependency-free protocol engine covering the command subset the
 * PicoDesk toolchain uses: CONNECT/status, MTA + UPLOAD/DOWNLOAD, CAL page
 * management (RTE-003), and dynamic DAQ lists fed from coherent fast-loop
 * frames (RTE-005). Compiles natively for the protocol test suite.
 *
 * NOTE (SRS CAL-001): the production target is Vector XCPlite v5. This core
 * implements the same wire protocol behind the same seams (transport send
 * callback, CAL page hooks, event-driven DAQ) so XCPlite can replace it
 * without touching the CDC transport or the RTE. Tracked as Phase 3 debt.
 *
 * CAL page redirection: the master addresses one logical CAL window (the A2L
 * RAM segment). Every MTA access inside that window is redirected to the
 * current OFFLINE page, so edits are transactional until SET_CAL_PAGE arms
 * the step-boundary switch (RTE-003).
 */

#ifndef PICODESK_XCP_CORE_H
#define PICODESK_XCP_CORE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define XCP_MAX_CTO 32u
#define XCP_MAX_DTO 32u

#define XCP_MAX_DAQ_LISTS 2u
#define XCP_MAX_ODT_PER_LIST 4u
#define XCP_MAX_ENTRIES_PER_ODT 8u

typedef struct {
    uint32_t addr;
    uint8_t len;
} xcp_odt_entry_t;

typedef struct {
    xcp_odt_entry_t entries[XCP_MAX_ENTRIES_PER_ODT];
    uint8_t n_entries;
} xcp_odt_t;

typedef struct {
    xcp_odt_t odts[XCP_MAX_ODT_PER_LIST];
    uint8_t n_odts;
    uint16_t event;
    bool selected;
    bool running;
} xcp_daq_list_t;

typedef struct {
    /* CAL window (RTE-003): logical base/size + hooks into the RTE. */
    uint8_t *cal_logical_base;
    size_t cal_size;
    uint8_t *(*cal_offline)(void *user);
    void (*cal_request_switch)(void *user);
    uint8_t (*cal_active_page)(void *user);

    /* Address guard for raw MTA access outside the CAL window. Return true
     * to allow. Native tests pass NULL (permissive). */
    bool (*mem_ok)(void *user, uint32_t addr, uint32_t len, bool write);

    /* DAQ transmit: one DTO packet (PID + samples), unframed. */
    void (*dto_send)(void *user, const uint8_t *pkt, uint8_t len);

    void *user;

    /* --- internal state --- */
    bool connected;
    uint32_t mta;
    uint16_t n_daq_allocated;
    xcp_daq_list_t daq[XCP_MAX_DAQ_LISTS];
    uint16_t ptr_daq;
    uint8_t ptr_odt;
    uint8_t ptr_entry;
    bool daq_running;
} xcp_slave_t;

/* Zero state; hooks must already be populated by the caller. */
void xcp_init(xcp_slave_t *x);

/* Process one command packet; writes the response packet into resp
 * (>= XCP_MAX_CTO bytes) and returns its length (always >= 1). */
uint8_t xcp_command(xcp_slave_t *x, const uint8_t *cmd, uint8_t cmd_len,
                    uint8_t *resp);

/* Feed one coherent fast-loop frame (RTE-005) to event channel 0. Emits a
 * DTO per running ODT whose entries fall inside
 * [frame_base_addr, frame_base_addr + frame_len). */
void xcp_daq_event0(xcp_slave_t *x, const uint8_t *frame, size_t frame_len,
                    uint32_t frame_base_addr);

#endif /* PICODESK_XCP_CORE_H */
