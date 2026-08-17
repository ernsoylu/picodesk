/* Cross-core signal bus + calibration layout for the spike firmware.
 *
 * Stand-in for Phase 5 generated code: two >32-bit buses crossing cores in
 * opposite directions (bi-directional seqlocks, RTE-004), one DAQ frame
 * layout (RTE-005), and one CAL parameter block (RTE-003). Everything is
 * word-aligned, fixed-size, and integer-only (MAT-002).
 */

#ifndef PICODESK_SPIKE_BUS_H
#define PICODESK_SPIKE_BUS_H

#include <stdint.h>

/* Fast -> slow (written by the core 0 ISR each 1 ms tick). */
typedef struct {
    uint32_t tick;
    int32_t torque_cmd;
    int32_t iq_meas;
} spike_fast_bus_t;

/* Slow -> fast (written by the 100 ms task on core 1). */
typedef struct {
    int32_t derate_q15;    /* 0..8192 output derating          */
    int32_t filter_shift;  /* plant filter tuning, 1..8        */
} spike_slow_bus_t;

/* Calibration page contents (RTE-003). */
typedef struct {
    int32_t kp_q15;
    int32_t ki_q15;
    int32_t trq_limit;
} spike_cal_t;

#define SPIKE_CAL_DEFAULTS { 9830, 655, 32767 }

/* One coherent DAQ frame per fast tick (RTE-005). */
typedef struct {
    uint32_t tick;
    int32_t torque_cmd;
    int32_t iq_meas;
    int32_t speed_est;
} spike_daq_frame_t;

#endif /* PICODESK_SPIKE_BUS_H */
