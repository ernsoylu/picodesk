#include "rte_daq_ring.h"

#include "rte_port.h"

static inline void copy_words(uint32_t *dst, const uint32_t *src, size_t words) {
    for (size_t i = 0; i < words; i++) {
        dst[i] = src[i];
    }
}

void rte_daq_ring_init(rte_daq_ring_t *ring, void *buf, size_t buf_bytes,
                       size_t frame_size) {
    ring->buf = (uint8_t *) buf;
    ring->frame_size = (uint32_t) frame_size;
    ring->capacity = (uint32_t) (buf_bytes / frame_size);
    ring->head = 0;
    ring->tail = 0;
    ring->dropped = 0;
}

bool RTE_TIME_CRITICAL(rte_daq_ring_push)(rte_daq_ring_t *ring, const void *frame) {
    const uint32_t head = ring->head;
    if (head - ring->tail >= ring->capacity) {
        ring->dropped++;
        return false;
    }
    uint8_t *slot = ring->buf + (head % ring->capacity) * ring->frame_size;
    copy_words((uint32_t *) slot, (const uint32_t *) frame, ring->frame_size / 4u);
    RTE_BARRIER(); /* frame contents land before the index publishes it */
    ring->head = head + 1;
    return true;
}

bool RTE_TIME_CRITICAL(rte_daq_ring_pop)(rte_daq_ring_t *ring, void *frame_out) {
    const uint32_t tail = ring->tail;
    if (ring->head == tail) {
        return false;
    }
    RTE_BARRIER(); /* index read before frame read */
    const uint8_t *slot = ring->buf + (tail % ring->capacity) * ring->frame_size;
    copy_words((uint32_t *) frame_out, (const uint32_t *) slot, ring->frame_size / 4u);
    RTE_BARRIER(); /* frame read completes before the slot is released */
    ring->tail = tail + 1;
    return true;
}
