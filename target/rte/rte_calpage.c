#include "rte_calpage.h"

#include <string.h>

#include "rte_port.h"

void rte_calpage_init(rte_calpage_t *cp, void *page_a, void *page_b,
                      size_t size, const void *defaults) {
    cp->pages[0] = (uint8_t *) page_a;
    cp->pages[1] = (uint8_t *) page_b;
    cp->size = size;
    memcpy(page_a, defaults, size);
    memcpy(page_b, defaults, size);
    cp->active_idx = 0;
    cp->switch_request = 0;
    cp->switch_count = 0;
}

void rte_calpage_request_switch(rte_calpage_t *cp) {
    RTE_BARRIER(); /* all offline-page writes land before the request */
    cp->switch_request = 1;
}

bool RTE_TIME_CRITICAL(rte_calpage_commit)(rte_calpage_t *cp) {
    if (cp->switch_request == 0) {
        return false;
    }
    cp->active_idx ^= 1u;
    RTE_BARRIER();
    cp->switch_request = 0;
    cp->switch_count++;
    return true;
}

bool rte_calpage_sync_offline(rte_calpage_t *cp) {
    if (cp->switch_request != 0) {
        return false;
    }
    memcpy(rte_calpage_offline(cp), rte_calpage_active(cp), cp->size);
    return true;
}
