#include "rte_seqlock.h"

#include "rte_port.h"

static inline void copy_words(uint32_t *dst, const uint32_t *src, size_t words) {
    for (size_t i = 0; i < words; i++) {
        dst[i] = src[i];
    }
}

void RTE_TIME_CRITICAL(rte_seqlock_write)(rte_seqlock_t *lock, void *dst, const void *src, size_t bytes) {
    const size_t words = bytes / 4u;
    const uint32_t irq_state = rte_port_irq_save();

    lock->seq++; /* odd: write in progress */
    RTE_BARRIER();
    copy_words((uint32_t *) dst, (const uint32_t *) src, words);
    RTE_BARRIER();
    lock->seq++; /* even: stable */

    rte_port_irq_restore(irq_state);
}

bool RTE_TIME_CRITICAL(rte_seqlock_read)(rte_seqlock_t *lock, void *dst, const void *src, size_t bytes) {
    /* Snapshot lands in a stack temp and reaches dst only when verified
     * consistent — a failed read must leave the caller's last-known-good
     * shadow completely untouched (RTE-004). */
    uint32_t tmp[RTE_SEQLOCK_MAX_WORDS];
    const size_t words = bytes / 4u;
    if (words > RTE_SEQLOCK_MAX_WORDS) {
        return false; /* contract violation: payload too large */
    }

    for (unsigned attempt = 0; attempt < RTE_SEQLOCK_MAX_RETRIES; attempt++) {
        const uint32_t seq_before = lock->seq;
        if (seq_before & 1u) {
            continue; /* write in progress */
        }
        RTE_BARRIER();
        copy_words(tmp, (const uint32_t *) src, words);
        RTE_BARRIER();
        if (lock->seq == seq_before) {
            copy_words((uint32_t *) dst, tmp, words);
            return true;
        }
    }
    return false; /* caller keeps last-known-good shadow (RTE-004) */
}
