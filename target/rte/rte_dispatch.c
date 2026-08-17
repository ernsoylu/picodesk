/* Multi-rate dispatcher — Phase 1 real-time spike (RTE-002).
 *
 * The fastest rate group runs in a core 0 hardware timer alarm ISR pinned to
 * SRAM (BLD-001); slower rate groups are FreeRTOS tasks on core 1 released via
 * vTaskNotifyGiveFromISR(). The ISR never takes the kernel spinlock except
 * inside that FromISR call — the one kernel crossing the NFR-3 budget allows.
 *
 * Alarm scheduling is absolute: each tick re-arms deadline + period, and
 * missed periods are skipped forward and counted as overruns rather than
 * bursting to catch up.
 */

#include <string.h>

#include "FreeRTOS.h"
#include "task.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "hardware/timer.h"
#include "pico/platform.h"

#include "rte.h"
#include "rte_sections.h"
#include "spike_models.h"

#define RTE_ALARM_NUM 1
#define RTE_ALARM_IRQ TIMER_IRQ_1

/* Logic-analyzer instrumentation (NFR-1): pin 2 is high exactly while the
 * fast ISR runs (pulse start = dispatch instant, width = execution time);
 * pin 3 toggles on every counted overrun. */
#define RTE_PIN_ISR_ACTIVE 2
#define RTE_PIN_OVERRUN 3

rte_telemetry_t g_rte_telemetry RTE_SHARED;

/* FreeRTOS heap — application-allocated into SRAM2 (BLD-002). */
uint8_t ucHeap[ configTOTAL_HEAP_SIZE ] RTE_SHARED;

/* ISR-local state, owned by core 0 (BLD-002). */
static TaskHandle_t s_rate10_task RTE_CORE0_BSS;
static TaskHandle_t s_rate100_task RTE_CORE0_BSS;
static uint32_t s_period_us RTE_CORE0_BSS;
static uint32_t s_deadline_us RTE_CORE0_BSS;
static uint32_t s_div10 RTE_CORE0_BSS;
static uint32_t s_div100 RTE_CORE0_BSS;

extern uint32_t __core0_bss_start__, __core0_bss_end__;
extern uint32_t __core1_bss_start__, __core1_bss_end__;
extern uint32_t __rte_shared_start__, __rte_shared_end__;

void rte_init(void) {
    /* NOLOAD sections carry random power-on contents: zero them explicitly so
     * every routed signal starts from zero/producer defaults (RTE-004). */
    memset(&__core0_bss_start__, 0,
           (size_t) ((uintptr_t) &__core0_bss_end__ - (uintptr_t) &__core0_bss_start__));
    memset(&__core1_bss_start__, 0,
           (size_t) ((uintptr_t) &__core1_bss_end__ - (uintptr_t) &__core1_bss_start__));
    memset(&__rte_shared_start__, 0,
           (size_t) ((uintptr_t) &__rte_shared_end__ - (uintptr_t) &__rte_shared_start__));

    gpio_init(RTE_PIN_ISR_ACTIVE);
    gpio_set_dir(RTE_PIN_ISR_ACTIVE, GPIO_OUT);
    gpio_init(RTE_PIN_OVERRUN);
    gpio_set_dir(RTE_PIN_OVERRUN, GPIO_OUT);
}

static void __not_in_flash_func(rte_fast_tick_isr)(void) {
    const uint32_t t_entry = timer_hw->timerawl;
    hw_clear_bits(&timer_hw->intr, 1u << RTE_ALARM_NUM);
    gpio_put(RTE_PIN_ISR_ACTIVE, 1);

    /* Dispatch latency relative to the programmed deadline (NFR-1 proxy). */
    const uint32_t late = t_entry - s_deadline_us;
    if (late < 0x80000000u && late > g_rte_telemetry.dispatch_jitter_max_us) {
        g_rte_telemetry.dispatch_jitter_max_us = late;
    }

    /* Absolute re-arm; a deadline already in the past is an overrun. */
    uint32_t next = s_deadline_us + s_period_us;
    while ((int32_t) (next - timer_hw->timerawl) <= 2) {
        next += s_period_us;
        g_rte_telemetry.overrun_count++;
        gpio_xor_mask(1u << RTE_PIN_OVERRUN);
    }
    s_deadline_us = next;
    timer_hw->alarm[RTE_ALARM_NUM] = next;

    spike_fast_step();

    g_rte_telemetry.heartbeat++;
    g_rte_telemetry.fast_ticks++;

    BaseType_t woken = pdFALSE;
    if (s_rate10_task != NULL && ++s_div10 >= 10u) {
        s_div10 = 0;
        vTaskNotifyGiveFromISR(s_rate10_task, &woken);
    }
    if (s_rate100_task != NULL && ++s_div100 >= 100u) {
        s_div100 = 0;
        vTaskNotifyGiveFromISR(s_rate100_task, &woken);
    }

    const uint32_t exec = timer_hw->timerawl - t_entry;
    g_rte_telemetry.exec_last_us = exec;
    if (exec > g_rte_telemetry.exec_max_us) {
        g_rte_telemetry.exec_max_us = exec;
    }
    g_rte_telemetry.exec_accum_us += exec;
    if (exec > s_period_us) {
        g_rte_telemetry.overrun_count++;
        gpio_xor_mask(1u << RTE_PIN_OVERRUN);
    }

    gpio_put(RTE_PIN_ISR_ACTIVE, 0);
    portYIELD_FROM_ISR(woken);
}

void rte_dispatch_start(uint32_t fast_period_us) {
    s_period_us = fast_period_us;

    hardware_alarm_claim(RTE_ALARM_NUM);
    irq_set_exclusive_handler(RTE_ALARM_IRQ, rte_fast_tick_isr);
    /* Highest NVIC priority on core 0: nothing preempts the fast path. */
    irq_set_priority(RTE_ALARM_IRQ, 0);

    s_deadline_us = timer_hw->timerawl + fast_period_us;
    timer_hw->alarm[RTE_ALARM_NUM] = s_deadline_us;
    hw_set_bits(&timer_hw->inte, 1u << RTE_ALARM_NUM);
    irq_set_enabled(RTE_ALARM_IRQ, true); /* on the calling core = core 0 */
}

void rte_dispatch_register(void *rate10_task, void *rate100_task) {
    s_rate100_task = (TaskHandle_t) rate100_task;
    __compiler_memory_barrier();
    s_rate10_task = (TaskHandle_t) rate10_task;
}
