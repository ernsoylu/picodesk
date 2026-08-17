/* Bi-directional bounded seqlock for cross-core signals >32 bits (RTE-004).
 *
 * Writer side masks local IRQs only — never the kernel SMP spinlock (NFR-3) —
 * so a write is atomic against preemption on its own core while the other
 * core's reader detects overlap via the sequence word. Readers retry at most
 * RTE_SEQLOCK_MAX_RETRIES times, then keep their last-known-good shadow and
 * report failure (the caller counts it as a seqlock fault, BLD-003).
 *
 * Payload size must be a multiple of 4; copies are open-coded word loops so
 * the fast path never calls out to libc/ROM memcpy (BLD-001).
 */

#ifndef PICODESK_RTE_SEQLOCK_H
#define PICODESK_RTE_SEQLOCK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define RTE_SEQLOCK_MAX_RETRIES 3

/* Largest protected payload (words): bounds the reader's verification buffer
 * on the ISR stack. 64 bytes covers every VFB bus struct; the RTE generator
 * must reject larger signal groups at generation time. */
#define RTE_SEQLOCK_MAX_WORDS 16

typedef struct {
    volatile uint32_t seq; /* odd while a write is in progress */
} rte_seqlock_t;

/* Publish src into the seqlock-protected buffer dst. Writer-exclusive per
 * lock (single-writer, GUI-009); safe from ISR or task context. */
void rte_seqlock_write(rte_seqlock_t *lock, void *dst, const void *src, size_t bytes);

/* Copy the protected buffer src into dst. Returns false when every retry saw
 * a concurrent write — dst is then untouched (last-known-good stale data). */
bool rte_seqlock_read(rte_seqlock_t *lock, void *dst, const void *src, size_t bytes);

#endif /* PICODESK_RTE_SEQLOCK_H */
