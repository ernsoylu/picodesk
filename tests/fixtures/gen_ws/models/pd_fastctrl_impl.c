/* Stand-in for ERT-generated FastCtrl step code (Phase 5 E2E fixture).
 * Self-exciting square wave so the cross-core coupling is observable even
 * with a zero ADC reading; pure integer math (MAT-002). */

#include "pd_FastCtrl_io.h"

static int32_t s_phase;

void pd_FastCtrl_init(void) {
    s_phase = 0;
}

void pd_FastCtrl_step(const pd_FastCtrl_in_t *in, pd_FastCtrl_out_t *out) {
    s_phase++;
    const int32_t base = ((s_phase >> 8) & 1) ? 9000 : -9000;
    int32_t torque = base + (int32_t) (in->adc_u >> 4)
                     - (int32_t) in->derate_in * 16;
    if (torque > 32000) torque = 32000;
    if (torque < -32000) torque = -32000;
    out->torque_cmd = (int16_t) torque;
}
