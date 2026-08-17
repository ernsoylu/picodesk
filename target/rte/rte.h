/* RTE core API and execution-health telemetry.
 *
 * Telemetry (BLD-003) lives in .rte_shared (SRAM2, BLD-002) and is written by
 * the core 0 fast ISR; core 1 (stats/XCP task) only reads it. All fields are
 * 32-bit so torn reads stay harmless on M0+. The heartbeat feeds the
 * cross-core watchdog (BLD-007).
 */

#ifndef PICODESK_RTE_H
#define PICODESK_RTE_H

#include <stdbool.h>
#include <stdint.h>

#include "spike_bus.h"

typedef struct {
    volatile uint32_t heartbeat;               /* BLD-007: +1 every fast tick   */
    volatile uint32_t fast_ticks;
    volatile uint32_t overrun_count;           /* BLD-003 + missed deadlines    */
    volatile uint32_t exec_last_us;            /* fast step execution time      */
    volatile uint32_t exec_max_us;
    volatile uint64_t exec_accum_us;           /* for ISR utilization %         */
    volatile uint32_t dispatch_jitter_max_us;  /* NFR-1 proxy (LA is the truth) */
    volatile uint32_t seqlock_fault_count;     /* RTE-004 stale fallbacks       */
    volatile uint32_t crit_max_us;             /* NFR-3 probe, worst hold time  */
    volatile uint32_t rate10_activations;
    volatile uint32_t rate100_activations;
    volatile uint32_t daq_frames_drained;      /* consumer-side count (RTE-005) */
} rte_telemetry_t;

extern rte_telemetry_t g_rte_telemetry;

/* Zero .core0_bss/.core1_bss/.rte_shared, then set producer defaults and
 * initialize seqlocks, CAL pages, and the DAQ ring (RTE-004, RTE-003,
 * RTE-005). Must run before any other rte_/spike_ call; never touches
 * .noinit_fault. */
void rte_init(void);

/* Arm the core 0 hardware timer alarm for the fast rate group (RTE-002). */
void rte_dispatch_start(uint32_t fast_period_us);

/* Hand the core 1 rate-group task handles to the dispatcher (RTE-002). */
void rte_dispatch_register(void *rate10_task, void *rate100_task);

/* --- Cross-core signal access (RTE-001/RTE-004) ------------------------- */

/* Core 1: copy the fast->slow bus into the caller's persistent shadow.
 * On seqlock exhaustion the shadow keeps last-known-good and the global
 * fault counter increments. Each caller owns its own shadow. */
void rte_fast_bus_read(spike_fast_bus_t *shadow);

/* Core 1, single writer (GUI-009: the 100 ms rate group): publish the
 * slow->fast bus. */
void rte_slow_bus_write(const spike_slow_bus_t *value);

/* Core 1: 32-bit speed signal (atomic on M0+, no seqlock needed). */
void rte_speed_est_write(int32_t speed);

/* --- DAQ ring (RTE-005) ------------------------------------------------- */

bool rte_daq_pop_frame(spike_daq_frame_t *out); /* core 1 consumer */
uint32_t rte_daq_depth(void);
uint32_t rte_daq_dropped(void);

/* --- Calibration pages (RTE-003) ---------------------------------------- */

spike_cal_t *rte_cal_offline(void);       /* core 1 edit target            */
void rte_cal_request_switch(void);        /* core 1: arm transactional swap */
bool rte_cal_sync_offline(void);          /* core 1: re-base after commit   */
void rte_cal_get_active(spike_cal_t *out);/* core 1 diagnostic snapshot     */
uint32_t rte_cal_switch_count(void);

#endif /* PICODESK_RTE_H */
