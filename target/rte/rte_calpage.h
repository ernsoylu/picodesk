/* XCP calibration pages with step-boundary transactional switch (RTE-003).
 *
 * Two equal-size RAM pages live in SRAM2. Core 1 (the XCP task) only ever
 * writes the OFFLINE page; core 0 flips the active index exclusively at the
 * model_step() boundary when a switch was requested, so a multi-parameter
 * change becomes visible to the fast loop atomically. After a switch, core 1
 * calls rte_calpage_sync_offline() to re-base the new offline page on the
 * live values — the fast path never copies pages (NFR-3).
 *
 * NvM is out of scope for v1.0: both pages boot from compiled defaults.
 */

#ifndef PICODESK_RTE_CALPAGE_H
#define PICODESK_RTE_CALPAGE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint8_t *pages[2];
    size_t size;
    volatile uint32_t active_idx;     /* flipped by core 0 only            */
    volatile uint32_t switch_request; /* set by core 1, cleared by core 0  */
    volatile uint32_t switch_count;   /* commits observed (telemetry)      */
} rte_calpage_t;

/* Both pages start as copies of defaults; page 0 becomes active. */
void rte_calpage_init(rte_calpage_t *cp, void *page_a, void *page_b,
                      size_t size, const void *defaults);

/* Fast-path read side (core 0): pointer to the active page. */
static inline const void *rte_calpage_active(const rte_calpage_t *cp) {
    return cp->pages[cp->active_idx & 1u];
}

/* Core 1: the page SET_CAL_PAGE/DOWNLOAD writes into. */
static inline void *rte_calpage_offline(rte_calpage_t *cp) {
    return cp->pages[(cp->active_idx & 1u) ^ 1u];
}

/* Core 1: arm the transactional switch (XCP SET_CAL_PAGE). */
void rte_calpage_request_switch(rte_calpage_t *cp);

/* Core 0, at the model_step() boundary ONLY. Returns true when a pending
 * switch was committed this step. */
bool rte_calpage_commit(rte_calpage_t *cp);

/* Core 1, after observing a commit: copy active -> offline so subsequent
 * edits start from the live values. Returns false while a switch is still
 * pending (offline page must not be touched mid-handshake). */
bool rte_calpage_sync_offline(rte_calpage_t *cp);

#endif /* PICODESK_RTE_CALPAGE_H */
