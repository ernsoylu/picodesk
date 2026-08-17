/* Post-mortem fault record surviving reset (BLD-006).
 *
 * Lives at the start of .noinit_fault (SRAM2) — a NOLOAD section that crt0
 * never zeroes and rte_init() deliberately skips — so the record written by
 * a HardFault or a failed assert is still readable after the watchdog
 * resets the chip (BLD-007), and is printed on the next boot.
 *
 * Cortex-M0+ has no CFSR/HFSR fault-status registers, so the record carries
 * the exception-stacked frame (PC/LR/xPSR + R0-R3, R12), which is what
 * actually localizes the fault.
 */

#ifndef PICODESK_FAULT_RECORD_H
#define PICODESK_FAULT_RECORD_H

#include <stdbool.h>
#include <stdint.h>

#define FAULT_MAGIC 0xFA017EC0u

typedef enum {
    FAULT_KIND_NONE = 0,
    FAULT_KIND_HARDFAULT = 1,
    FAULT_KIND_ASSERT = 2,
    FAULT_KIND_STACK_OVERFLOW = 3,
    FAULT_KIND_MALLOC_FAILED = 4,
} fault_kind_t;

typedef struct {
    uint32_t magic;      /* FAULT_MAGIC when the record is valid   */
    uint32_t kind;       /* fault_kind_t                           */
    uint32_t pc;         /* stacked PC (fault) or file ptr (assert)*/
    uint32_t lr;         /* stacked LR (fault) or line (assert)    */
    uint32_t psr;
    uint32_t r0, r1, r2, r3, r12;
    uint32_t core;       /* core that faulted                      */
    uint32_t heartbeat;  /* fast-loop heartbeat at fault time      */
    uint32_t seq;        /* +1 per record, survives resets         */
} fault_record_t;

extern volatile fault_record_t g_fault_record;

/* Called from the naked HardFault handler with the stacked frame. */
void fault_capture_hardfault(uint32_t *stacked_frame);

/* Non-exception faults (assert, stack overflow, malloc). */
void fault_capture_sw(fault_kind_t kind, uint32_t a, uint32_t b);

/* True when a valid record from a previous run is present. */
bool fault_record_valid(void);

/* Print any surviving record, then invalidate it so the next boot is
 * clean. Safe to call before the scheduler starts. */
void fault_report_boot(void);

#endif /* PICODESK_FAULT_RECORD_H */
