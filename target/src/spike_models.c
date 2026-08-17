/* Toy models: a Q15 PI current loop with a first-order plant (fast), a speed
 * observer (10 ms), and a thermal-style derate filter (100 ms). Pure integer
 * math throughout (MAT-002: no software float near the fast path).
 */

#include "spike_models.h"

#include "pico/platform.h"

#include "rte_sections.h"

/* Fast-loop state, core 0 only (BLD-002). */
static int32_t s_integrator RTE_CORE0_BSS;
static int32_t s_plant RTE_CORE0_BSS;
static uint32_t s_phase RTE_CORE0_BSS;

void __not_in_flash_func(spike_fast_step)(const spike_cal_t *cal,
                                          const spike_slow_bus_t *in,
                                          spike_fast_bus_t *out) {
    /* Square-wave setpoint so the loop keeps doing real work. */
    s_phase++;
    const int32_t setpoint = ((s_phase >> 9) & 1u) ? 12000 : -12000;
    const int32_t err = ((setpoint * (32768 - in->derate_q15)) >> 15) - s_plant;

    s_integrator += (cal->ki_q15 * err) >> 15;
    if (s_integrator > 20000) s_integrator = 20000;
    if (s_integrator < -20000) s_integrator = -20000;

    int32_t cmd = ((cal->kp_q15 * err) >> 15) + s_integrator;
    if (cmd > cal->trq_limit) cmd = cal->trq_limit;
    if (cmd < -cal->trq_limit) cmd = -cal->trq_limit;

    /* First-order plant response; time constant tuned from the slow bus. */
    int32_t shift = in->filter_shift;
    if (shift < 1) shift = 1;
    if (shift > 8) shift = 8;
    s_plant += (cmd - s_plant) >> shift;

    out->torque_cmd = cmd;
    out->iq_meas = s_plant;
}

int32_t spike_10ms_step(const spike_fast_bus_t *in) {
    static int32_t speed;
    speed += ((in->iq_meas / 8) - speed) >> 3;
    return speed;
}

void spike_100ms_step(const spike_fast_bus_t *in, spike_slow_bus_t *out) {
    static int32_t temp;
    const int32_t load = in->torque_cmd < 0 ? -in->torque_cmd : in->torque_cmd;
    temp += ((load / 4) - temp) >> 5;
    out->derate_q15 = temp > 8192 ? 8192 : temp;
    out->filter_shift = 4;
}
