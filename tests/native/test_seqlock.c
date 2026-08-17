/* Seqlock (RTE-004) unit + pthread stress tests.
 *
 * The stress test is the native stand-in for the on-target two-core torture
 * test: a writer thread continuously publishes internally-consistent payloads
 * (all words equal) while a reader verifies it never observes a torn mix.
 */

#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <string.h>

#include "mini_test.h"
#include "rte_seqlock.h"

#define WORDS 12

typedef struct {
    uint32_t w[WORDS];
} payload_t;

static rte_seqlock_t lock;
static payload_t shared_buf;
static atomic_bool stop;
static atomic_ulong torn_reads;
static atomic_ulong good_reads;
static atomic_ulong stale_reads;

static void *writer(void *arg) {
    (void) arg;
    payload_t p;
    uint32_t v = 1;
    while (!atomic_load(&stop)) {
        for (int i = 0; i < WORDS; i++) {
            p.w[i] = v;
        }
        rte_seqlock_write(&lock, &shared_buf, &p, sizeof p);
        v++;
    }
    return NULL;
}

static void *reader(void *arg) {
    (void) arg;
    payload_t shadow;
    memset(&shadow, 0, sizeof shadow);
    while (!atomic_load(&stop)) {
        if (rte_seqlock_read(&lock, &shadow, &shared_buf, sizeof shadow)) {
            atomic_fetch_add(&good_reads, 1);
        } else {
            atomic_fetch_add(&stale_reads, 1);
        }
        for (int i = 1; i < WORDS; i++) {
            if (shadow.w[i] != shadow.w[0]) {
                atomic_fetch_add(&torn_reads, 1);
                break;
            }
        }
    }
    return NULL;
}

int main(void) {
    /* Unit: round trip. */
    payload_t src, dst;
    memset(&dst, 0, sizeof dst);
    for (int i = 0; i < WORDS; i++) {
        src.w[i] = 0xA0A0A0A0u + (uint32_t) i;
    }
    rte_seqlock_write(&lock, &shared_buf, &src, sizeof src);
    MT_CHECK(rte_seqlock_read(&lock, &dst, &shared_buf, sizeof dst));
    MT_CHECK_EQ(memcmp(&src, &dst, sizeof src), 0);
    MT_CHECK_EQ(lock.seq % 2, 0);

    /* Unit: reader gives up (bounded, RTE-004) while a write is in flight
     * and leaves the last-known-good shadow untouched. */
    lock.seq = 1; /* simulate writer mid-flight */
    payload_t before = dst;
    MT_CHECK(!rte_seqlock_read(&lock, &dst, &shared_buf, sizeof dst));
    MT_CHECK_EQ(memcmp(&before, &dst, sizeof before), 0);
    lock.seq = 2;

    /* Stress: no torn read may ever survive the retry protocol. */
    pthread_t wt, rt1, rt2;
    pthread_create(&wt, NULL, writer, NULL);
    pthread_create(&rt1, NULL, reader, NULL);
    pthread_create(&rt2, NULL, reader, NULL);
    struct timespec t = {2, 0};
    nanosleep(&t, NULL);
    atomic_store(&stop, true);
    pthread_join(wt, NULL);
    pthread_join(rt1, NULL);
    pthread_join(rt2, NULL);

    MT_CHECK_EQ(atomic_load(&torn_reads), 0);
    MT_CHECK(atomic_load(&good_reads) > 1000);
    printf("stress: good=%lu stale=%lu torn=%lu\n", atomic_load(&good_reads),
           atomic_load(&stale_reads), atomic_load(&torn_reads));

    return mt_summary("seqlock");
}
