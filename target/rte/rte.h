/* RTE core API and execution-health telemetry (hardware-free header).
 *
 * Telemetry (BLD-003) lives in .rte_shared (SRAM2, BLD-002) and is written by
 * the core 0 fast ISR; core 1 (stats/XCP task) only reads it. All fields are
 * 32-bit or written before the heartbeat so torn reads stay harmless on M0+.
 * The heartbeat feeds the cross-core watchdog (BLD-007).
 */

#ifndef PICODESK_RTE_H
#define PICODESK_RTE_H

#include <stdint.h>

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
} rte_telemetry_t;

extern rte_telemetry_t g_rte_telemetry;

/* Zero .core0_bss/.core1_bss/.rte_shared and set boot defaults (RTE-004).
 * Must run before any other rte_/spike_ call. Never touches .noinit_fault. */
void rte_init(void);

/* Arm the core 0 hardware timer alarm for the fast rate group (RTE-002).
 * Call from core 0; safe before the scheduler starts (task notification is
 * gated until rte_dispatch_register()). */
void rte_dispatch_start(uint32_t fast_period_us);

/* Hand the core 1 rate-group task handles to the dispatcher (RTE-002).
 * Handles are TaskHandle_t; typed void* to keep this header kernel-free. */
void rte_dispatch_register(void *rate10_task, void *rate100_task);

#endif /* PICODESK_RTE_H */
