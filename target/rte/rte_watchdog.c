#include "rte_watchdog.h"

#include "FreeRTOS.h"
#include "task.h"
#include "hardware/watchdog.h"

#include "rte_sections.h"

static volatile uint32_t s_last_heartbeat RTE_CORE1_BSS;
static volatile uint32_t s_stall_count RTE_CORE1_BSS;
static volatile uint32_t s_inject_stall RTE_CORE1_BSS;

void rte_watchdog_init(void) {
    /* pause_on_debug=false: v1.0 has no on-target debugger (SRS 1.3). */
    watchdog_enable(RTE_WDG_TIMEOUT_MS, false);
}

void rte_watchdog_task(void *arg) {
    volatile uint32_t *heartbeat = (volatile uint32_t *) arg;
    s_last_heartbeat = *heartbeat;

    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(RTE_WDG_PERIOD_MS));

        const uint32_t now = *heartbeat;
        const bool advanced = (now != s_last_heartbeat);
        s_last_heartbeat = now;

        if (!advanced) {
            /* Core 0 fast path is wedged: withhold the feed and let the
             * hardware watchdog reset the chip (BLD-007). */
            s_stall_count++;
            continue;
        }
        if (s_inject_stall) {
            continue; /* injected monitor stall (test hook) */
        }
        watchdog_update();
    }
}

void rte_watchdog_inject_stall(void) {
    s_inject_stall = 1u;
}

uint32_t rte_watchdog_stall_count(void) {
    return s_stall_count;
}
