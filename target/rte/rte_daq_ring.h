/* Coherent DAQ data path (RTE-005): single-producer / single-consumer ring of
 * fixed-size frames in SRAM2.
 *
 * The core 0 fast ISR pushes whole-frame snapshots; the core 1 XCP task pops
 * them. Index ordering alone guarantees frame coherence — no locks, no torn
 * frames. A full ring drops the new frame and counts it (never blocks the
 * fast path). Frame size must be a multiple of 4.
 */

#ifndef PICODESK_RTE_DAQ_RING_H
#define PICODESK_RTE_DAQ_RING_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint8_t *buf;
    uint32_t frame_size;
    uint32_t capacity;            /* frames; power of two NOT required */
    volatile uint32_t head;       /* frames produced, written by core 0 only */
    volatile uint32_t tail;       /* frames consumed, written by core 1 only */
    volatile uint32_t dropped;    /* full-ring drops (BLD-003) */
} rte_daq_ring_t;

void rte_daq_ring_init(rte_daq_ring_t *ring, void *buf, size_t buf_bytes,
                       size_t frame_size);

/* Producer (core 0 ISR only). Returns false and counts a drop when full. */
bool rte_daq_ring_push(rte_daq_ring_t *ring, const void *frame);

/* Consumer (core 1 only). Returns false when empty. */
bool rte_daq_ring_pop(rte_daq_ring_t *ring, void *frame_out);

static inline uint32_t rte_daq_ring_count(const rte_daq_ring_t *ring) {
    return ring->head - ring->tail;
}

#endif /* PICODESK_RTE_DAQ_RING_H */
