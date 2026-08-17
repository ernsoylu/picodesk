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

#else /* RP2040 */

#include "hardware/sync.h"
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

#endif /* PICODESK_NATIVE */

#endif /* PICODESK_RTE_PORT_H */
