/* DAQ ring (RTE-005) unit + SPSC pthread stress tests. */

#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <string.h>

#include "mini_test.h"
#include "rte_daq_ring.h"

typedef struct {
    uint32_t seq;
    uint32_t payload[3];
} frame_t;

#define CAPACITY 8

static rte_daq_ring_t ring;
static uint8_t buf[CAPACITY * sizeof(frame_t)];

static atomic_bool stop;
static atomic_ulong incoherent;
static atomic_ulong out_of_order;
static atomic_ulong consumed;

static void *producer(void *arg) {
    (void) arg;
    frame_t f;
    uint32_t seq = 0;
    while (!atomic_load(&stop)) {
        f.seq = seq;
        for (int i = 0; i < 3; i++) {
            f.payload[i] = seq * 3u + (uint32_t) i;
        }
        if (rte_daq_ring_push(&ring, &f)) {
            seq++;
        }
    }
    return NULL;
}

static void *consumer(void *arg) {
    (void) arg;
    frame_t f;
    uint32_t expected = 0;
    while (!atomic_load(&stop)) {
        if (!rte_daq_ring_pop(&ring, &f)) {
            continue;
        }
        atomic_fetch_add(&consumed, 1);
        if (f.seq != expected) {
            atomic_fetch_add(&out_of_order, 1);
            expected = f.seq;
        }
        for (int i = 0; i < 3; i++) {
            if (f.payload[i] != f.seq * 3u + (uint32_t) i) {
                atomic_fetch_add(&incoherent, 1);
                break;
            }
        }
        expected++;
    }
    return NULL;
}

int main(void) {
    rte_daq_ring_init(&ring, buf, sizeof buf, sizeof(frame_t));
    MT_CHECK_EQ(ring.capacity, CAPACITY);

    /* Unit: fill to capacity, overflow drops + counts, drain restores order. */
    frame_t f = {0, {0, 0, 0}}, out;
    for (uint32_t i = 0; i < CAPACITY; i++) {
        f.seq = i;
        MT_CHECK(rte_daq_ring_push(&ring, &f));
    }
    MT_CHECK(!rte_daq_ring_push(&ring, &f)); /* full */
    MT_CHECK_EQ(ring.dropped, 1);
    MT_CHECK_EQ(rte_daq_ring_count(&ring), CAPACITY);
    for (uint32_t i = 0; i < CAPACITY; i++) {
        MT_CHECK(rte_daq_ring_pop(&ring, &out));
        MT_CHECK_EQ(out.seq, i);
    }
    MT_CHECK(!rte_daq_ring_pop(&ring, &out)); /* empty */

    /* SPSC stress: frames must arrive whole (coherent) and in order. Drops
     * are legal (full ring); reordering or tearing is not. */
    pthread_t pt, ct;
    pthread_create(&pt, NULL, producer, NULL);
    pthread_create(&ct, NULL, consumer, NULL);
    struct timespec t = {2, 0};
    nanosleep(&t, NULL);
    atomic_store(&stop, true);
    pthread_join(pt, NULL);
    pthread_join(ct, NULL);

    MT_CHECK_EQ(atomic_load(&incoherent), 0);
    MT_CHECK_EQ(atomic_load(&out_of_order), 0);
    MT_CHECK(atomic_load(&consumed) > 1000);
    printf("stress: consumed=%lu dropped=%u\n", atomic_load(&consumed),
           ring.dropped);

    return mt_summary("daq_ring");
}
