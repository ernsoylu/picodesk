/* Coherent DAQ data path (RTE-005).
 *
 * The Core 0 fast-path ISR snapshots complete DAQ frames into this SRAM2 ring;
 * the Core 1 XCPlite task drains it over USB CDC. Single-producer /
 * single-consumer, no locks — index ordering alone must guarantee frame
 * coherence (no torn frames) and avoid cross-bank contention.
 */

#ifndef PICODESK_RTE_DAQ_RING_H
#define PICODESK_RTE_DAQ_RING_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

void rte_daq_ring_init(void);

/* Core 0 only, from the fast ISR. Drops the frame (and counts it) when full. */
bool rte_daq_ring_push(const void *frame, size_t len);

/* Core 1 only, from the XCPlite task. */
bool rte_daq_ring_pop(void *frame, size_t max_len, size_t *out_len);

#endif /* PICODESK_RTE_DAQ_RING_H */
