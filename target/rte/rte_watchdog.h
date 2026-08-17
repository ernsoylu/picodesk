/* Cross-core watchdog monitor (BLD-007).
 *
 * Core 0's fast ISR increments a heartbeat every tick. A core 1 task
 * verifies the heartbeat actually advanced since its last check and only
 * then feeds the RP2040 hardware watchdog. A wedged fast path therefore
 * resets the chip even though core 1 is perfectly healthy — the failure
 * mode a plain "task feeds watchdog" design misses entirely.
 *
 * The reset is a watchdog reset, not a power cycle, so SRAM survives and
 * any fault record written before the wedge is printed on the next boot
 * (BLD-006).
 */

#ifndef PICODESK_RTE_WATCHDOG_H
#define PICODESK_RTE_WATCHDOG_H

#include <stdint.h>

/* Hardware watchdog timeout. Must exceed the monitor period with margin;
 * the monitor feeds every RTE_WDG_PERIOD_MS. */
#define RTE_WDG_TIMEOUT_MS 1000u
#define RTE_WDG_PERIOD_MS 100u

/* Arm the hardware watchdog. Call on core 0 before the scheduler starts;
 * the monitor task must exist or the chip resets within the timeout. */
void rte_watchdog_init(void);

/* Core 1 monitor task body: verify heartbeat advancement, then feed.
 * `arg` is a pointer to the volatile uint32_t heartbeat counter. */
void rte_watchdog_task(void *arg);

/* Test hook: stop feeding the hardware watchdog from the next check on,
 * simulating a wedged monitor (BLD-007 injection). */
void rte_watchdog_inject_stall(void);

/* Diagnostics: how many checks observed a stalled heartbeat. */
uint32_t rte_watchdog_stall_count(void);

#endif /* PICODESK_RTE_WATCHDOG_H */
