/* Hand-written toy rate-group steps for the Phase 1 real-time spike.
 * Stand-ins for generated ERT model code; deliberately integer/Q15-only
 * (MAT-002: no software float anywhere near the fast path).
 */

#ifndef PICODESK_SPIKE_MODELS_H
#define PICODESK_SPIKE_MODELS_H

#include <stdint.h>

/* 1 ms step, runs inside the core 0 fast ISR from SRAM (BLD-001). */
void spike_fast_step(void);

/* 10 ms / 100 ms steps, run in core 1 FreeRTOS tasks (RTE-002). */
void spike_10ms_step(void);
void spike_100ms_step(void);

#endif /* PICODESK_SPIKE_MODELS_H */
