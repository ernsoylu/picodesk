/* NFR-3 critical-section probe (target/rte/rte_port.h, rte_seqlock.c).
 *
 * The probe reports a duration in whole microseconds, and a healthy seqlock
 * write finishes well inside one tick — so on real hardware it reads zero.
 * That makes "working, and too fast to resolve" look exactly like "not in the
 * code path at all", which is the failure mode V-1 and V-2 already cost this
 * project once. These tests separate the two:
 *
 *   1. the max-tracking arithmetic is correct, including that a later smaller
 *      sample does not lower the recorded worst case;
 *   2. rte_seqlock_write() actually invokes the probe — the part that can
 *      silently stop being true if the macro or the build option drifts.
 *
 * What is deliberately NOT asserted is a specific duration. Timing a sub-
 * microsecond region on a loaded host would be a flaky test dressed up as a
 * measurement; the real number comes from the campaign in
 * docs/HARDWARE_CAMPAIGN.md.
 */

#include <stdint.h>
#include <string.h>

#include "mini_test.h"
#include "rte_port.h" /* declares rte_crit_probe_report under the probe option */
#include "rte_seqlock.h"

static rte_seqlock_t lock;
static uint32_t published[8];

int main(void) {
    rte_crit_probe_reset();
    MT_CHECK_EQ(rte_crit_samples(), 0u);
    MT_CHECK_EQ(rte_crit_max_us(), 0u);

    /* 1. max tracking, including the non-monotonic case. */
    rte_crit_probe_report(3);
    MT_CHECK_EQ(rte_crit_max_us(), 3u);
    rte_crit_probe_report(17);
    MT_CHECK_EQ(rte_crit_max_us(), 17u);
    rte_crit_probe_report(5); /* smaller: must not lower the worst case */
    MT_CHECK_EQ(rte_crit_max_us(), 17u);
    MT_CHECK_EQ(rte_crit_samples(), 3u);

    /* 2. the writer really is instrumented. */
    rte_crit_probe_reset();
    const uint32_t payload[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    for (unsigned i = 0; i < 100; i++) {
        rte_seqlock_write(&lock, published, payload, sizeof payload);
    }
    MT_CHECK_EQ(rte_crit_samples(), 100u);
    MT_CHECK_EQ(memcmp(published, payload, sizeof payload), 0);

    /* The write must still be correct with the probe compiled in — the
     * instrumentation sits inside the IRQ-disabled window, so a mistake there
     * would corrupt the sequence counter rather than just misreport. */
    MT_CHECK_EQ(lock.seq, 200u); /* two increments per write, ending even */

    return mt_summary("crit_probe");
}
