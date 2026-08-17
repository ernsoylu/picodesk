/* Bi-directional bounded seqlocks for cross-core signals >32 bits (RTE-004).
 *
 * Writer side disables local IRQs only — never the kernel SMP spinlock, which
 * would violate the 15 us global critical-section budget (NFR-3). Reader side
 * retries at most RTE_SEQLOCK_MAX_RETRIES times, then keeps last-known-good
 * stale data and increments the seqlock fault counter (BLD-003).
 * Instances live in SRAM2 (BLD-002).
 */

#ifndef PICODESK_RTE_SEQLOCK_H
#define PICODESK_RTE_SEQLOCK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define RTE_SEQLOCK_MAX_RETRIES 3

typedef struct {
    volatile uint32_t sequence; /* odd while a write is in progress */
} rte_seqlock_t;

void rte_seqlock_write(rte_seqlock_t *lock, void *dst, const void *src, size_t len);
bool rte_seqlock_read(rte_seqlock_t *lock, void *dst, const void *src, size_t len);

#endif /* PICODESK_RTE_SEQLOCK_H */
