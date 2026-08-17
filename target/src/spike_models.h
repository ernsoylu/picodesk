/* Hand-written toy rate-group steps for the real-time spike.
 * Stand-ins for generated ERT model code with RTE-001 copy-in semantics:
 * every step takes explicit inputs and produces explicit outputs; no step
 * reads shared buffers directly. Integer/Q15-only (MAT-002).
 */

#ifndef PICODESK_SPIKE_MODELS_H
#define PICODESK_SPIKE_MODELS_H

#include <stdint.h>

#include "spike_bus.h"

/* 1 ms step, runs inside the core 0 fast ISR from SRAM (BLD-001).
 * cal: active CAL page (RTE-003); in: copy-in shadow of the slow bus. */
void spike_fast_step(const spike_cal_t *cal, const spike_slow_bus_t *in,
                     spike_fast_bus_t *out);

/* 10 ms step (core 1): speed observer over the fast bus shadow. */
int32_t spike_10ms_step(const spike_fast_bus_t *in);

/* 100 ms step (core 1): thermal derate, producing the slow bus. */
void spike_100ms_step(const spike_fast_bus_t *in, spike_slow_bus_t *out);

#endif /* PICODESK_SPIKE_MODELS_H */
