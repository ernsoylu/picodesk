/* Stand-in for ERT-generated SlowSense step code (Phase 5 E2E fixture). */

#include "pd_SlowSense_io.h"

void pd_SlowSense_init(void) {
}

void pd_SlowSense_step(const pd_SlowSense_in_t *in, pd_SlowSense_out_t *out) {
    int32_t load = in->load_in;
    if (load < 0) load = -load;
    load >>= 6;
    out->derate_pct = (uint8_t) (load > 255 ? 255 : load);
}
