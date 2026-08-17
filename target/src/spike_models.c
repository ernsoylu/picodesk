/* Toy models: a Q15 PI current loop with a first-order plant (fast), a speed
 * aggregator (10 ms), and a slow thermal-style filter (100 ms). The shared
 * signals below stand in for VFB-routed signals; Phase 2 moves the >32-bit
 * cross-core paths onto seqlocks (RTE-004). All 32-bit writes are atomic on
 * M0+, so these toy signals cannot tear.
 */

#include "spike_models.h"

#include "pico/platform.h"

#include "rte_sections.h"

/* "Routed" demo signals (SRAM2). */
volatile int32_t g_sig_torque_cmd RTE_SHARED; /* fast -> HAL/PWM      */
volatile int32_t g_sig_iq_meas RTE_SHARED;    /* fast plant state     */
volatile int32_t g_sig_speed_est RTE_SHARED;  /* 10 ms -> consumers   */
volatile int32_t g_sig_derate RTE_SHARED;     /* 100 ms -> fast       */

/* Fast-loop state, core 0 only (BLD-002). */
static int32_t s_integrator RTE_CORE0_BSS;
static int32_t s_plant RTE_CORE0_BSS;
static uint32_t s_phase RTE_CORE0_BSS;

#define Q15_KP 9830  /* 0.30 */
#define Q15_KI 655   /* 0.02 */

void __not_in_flash_func(spike_fast_step)(void) {
    /* Square-wave setpoint so the loop keeps doing real work. */
    s_phase++;
    const int32_t setpoint = ((s_phase >> 9) & 1u) ? 12000 : -12000;
    const int32_t derate = g_sig_derate;
    const int32_t err = ((setpoint * (32768 - derate)) >> 15) - s_plant;

    s_integrator += (Q15_KI * err) >> 15;
    if (s_integrator > 20000) s_integrator = 20000;
    if (s_integrator < -20000) s_integrator = -20000;

    int32_t out = ((Q15_KP * err) >> 15) + s_integrator;
    if (out > 32767) out = 32767;
    if (out < -32768) out = -32768;

    /* First-order plant response. */
    s_plant += (out - s_plant) >> 4;

    g_sig_torque_cmd = out;
    g_sig_iq_meas = s_plant;
}

void spike_10ms_step(void) {
    /* Pretend speed observer: low-pass the plant state. */
    static int32_t speed;
    speed += ((g_sig_iq_meas / 8) - speed) >> 3;
    g_sig_speed_est = speed;
}

void spike_100ms_step(void) {
    /* Pretend thermal derating: creep toward a load-dependent target. */
    static int32_t temp;
    const int32_t load = g_sig_torque_cmd < 0 ? -g_sig_torque_cmd : g_sig_torque_cmd;
    temp += ((load / 4) - temp) >> 5;
    g_sig_derate = temp > 8192 ? 8192 : temp;
}
