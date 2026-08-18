/* Platform seam for the RTE primitives.
 *
 * The concurrency primitives (seqlock, DAQ ring, CAL pages) are pure C11 and
 * compile in two worlds: the RP2040 firmware, and a native host build
 * (PICODESK_NATIVE) used by tests/native to stress them under pthreads.
 */

#ifndef PICODESK_RTE_PORT_H
#define PICODESK_RTE_PORT_H

#include <stdint.h>

#ifdef PICODESK_NATIVE

#include <time.h>

/* Full fence natively: the pthread stress tests must hold on any host arch. */
#define RTE_BARRIER() __sync_synchronize()

/* No banked flash/SRAM split natively. */
#define RTE_TIME_CRITICAL( f ) f

static inline uint32_t rte_port_irq_save(void) {
    return 0;
}

static inline void rte_port_irq_restore(uint32_t state) {
    (void) state;
}

/* Real clock natively too, so tests/native can compile the NFR-3 probe below
 * and prove it actually fires rather than assuming it does. */
static inline uint32_t rte_port_now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t) (ts.tv_sec * 1000000u + (uint32_t) (ts.tv_nsec / 1000));
}

#else /* RP2040 */

#include "hardware/sync.h"
#include "hardware/timer.h"
#include "pico/platform.h"

/* Cortex-M0+ cores do not reorder memory accesses; a compiler barrier is
 * sufficient for cross-core ordering through the bus fabric. */
#define RTE_BARRIER() __compiler_memory_barrier()

/* RTE copy routines execute from SRAM on target (BLD-001). */
#define RTE_TIME_CRITICAL( f ) __not_in_flash_func( f )

static inline uint32_t rte_port_irq_save(void) {
    return save_and_disable_interrupts();
}

static inline void rte_port_irq_restore(uint32_t state) {
    restore_interrupts(state);
}

/* Low word of the free-running 64-bit timer. Reading timerawl alone is safe
 * (no latching handshake) and a difference of two reads is correct across the
 * 32-bit wrap, which is all the probe below needs. */
static inline uint32_t rte_port_now_us(void) {
    return timer_hw->timerawl;
}

#endif /* PICODESK_NATIVE */

/* NFR-3 probe: worst observed hold time of an IRQ-disabled critical section.
 *
 * OFF by default, and deliberately so — it adds two timer reads inside the
 * very window it measures, which is the wrong thing to ship on a 15 us budget.
 * Build with -DPICODESK_NFR3_PROBE=1 for bring-up.
 *
 * What it does and does not tell you. The RP2040 timer ticks at 1 us, and the
 * seqlock write is well under that, so on a healthy system the max reads 0 or
 * 1 — which is why the probe also counts samples. A max of 0 with a nonzero
 * count means "measured, and shorter than the clock can resolve"; a count of 0
 * means the probe is not in the path at all. Without the count those two are
 * the same number, and a probe you cannot distinguish from a dead one is worth
 * nothing.
 *
 * It also only sees the sections PicoDesk owns (the seqlock writer). NFR-3
 * bounds *any* critical section on either core, including FreeRTOS SMP and SDK
 * ones this cannot reach. It is a continuous regression signal, not the
 * verification — that is the probe campaign in docs/HARDWARE_CAMPAIGN.md.
 */
#ifdef PICODESK_NFR3_PROBE
void rte_crit_probe_report(uint32_t held_us);
#define RTE_CRIT_PROBE_ENTER() const uint32_t rte_crit_t0_ = rte_port_now_us()
#define RTE_CRIT_PROBE_EXIT() rte_crit_probe_report(rte_port_now_us() - rte_crit_t0_)
#else
#define RTE_CRIT_PROBE_ENTER() ((void) 0)
#define RTE_CRIT_PROBE_EXIT() ((void) 0)
#endif

#endif /* PICODESK_RTE_PORT_H */
