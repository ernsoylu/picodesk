/* Multi-rate dispatcher with the Phase 2 RTE primitives wired in.
 *
 * Fast ISR sequence each 1 ms tick (core 0, SRAM, BLD-001):
 *   1. re-arm the absolute deadline (missed periods counted, RTE-002)
 *   2. commit a pending CAL page switch — the step boundary (RTE-003)
 *   3. copy-in: slow->fast bus via bounded seqlock into a core-0 shadow
 *      (RTE-001/RTE-004; stale fallback counted)
 *   4. run the fast model step against active CAL page + shadow inputs
 *   5. publish fast->slow bus via seqlock; push a coherent DAQ frame
 *      (RTE-004/RTE-005)
 *   6. telemetry, heartbeat, rate-group notifications (BLD-003/BLD-007)
 *
 * The only kernel crossing is vTaskNotifyGiveFromISR (NFR-3 budget).
 */

#include <string.h>

#include "FreeRTOS.h"
#include "task.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "hardware/timer.h"
#include "pico/platform.h"

#include "rte.h"
#include "rte_calpage.h"
#include "rte_daq_ring.h"
#include "rte_sections.h"
#include "rte_seqlock.h"
#include "spike_models.h"

#include "fault_inject.h"

#define RTE_ALARM_NUM 1
#define RTE_ALARM_IRQ TIMER_IRQ_1

/* Logic-analyzer instrumentation (NFR-1): pin 2 is high exactly while the
 * fast ISR runs; pin 3 toggles on every counted overrun. */
#define RTE_PIN_ISR_ACTIVE 2
#define RTE_PIN_OVERRUN 3

#define RTE_DAQ_FRAMES 256 /* 4 kB ring in SRAM2 */

rte_telemetry_t g_rte_telemetry RTE_SHARED;

/* FreeRTOS heap — application-allocated into SRAM2 (BLD-002). */
uint8_t ucHeap[ configTOTAL_HEAP_SIZE ] RTE_SHARED;

/* Cross-core buses (SRAM2) + per-core shadows (owning core's bank). */
static rte_seqlock_t s_sl_fast RTE_SHARED;
static spike_fast_bus_t s_bus_fast RTE_SHARED;
static rte_seqlock_t s_sl_slow RTE_SHARED;
static spike_slow_bus_t s_bus_slow RTE_SHARED;
static spike_slow_bus_t s_shadow_slow RTE_CORE0_BSS; /* ISR copy-in target */
static volatile int32_t s_speed_est RTE_SHARED;      /* 32-bit, atomic     */

/* CAL pages (RTE-003) and DAQ ring (RTE-005), all in SRAM2. */
static spike_cal_t s_cal_page_a RTE_SHARED;
static spike_cal_t s_cal_page_b RTE_SHARED;
static rte_calpage_t s_calpage RTE_SHARED;
static uint8_t s_daq_buf[RTE_DAQ_FRAMES * sizeof(spike_daq_frame_t)] RTE_SHARED;
static rte_daq_ring_t s_daq RTE_SHARED;

/* ISR-local dispatcher state, core 0's bank (BLD-002). */
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

    /* Producer defaults (RTE-004): pre-scheduler, single-core, direct init. */
    s_bus_slow.derate_q15 = 0;
    s_bus_slow.filter_shift = 4;
    s_shadow_slow = s_bus_slow;

    const spike_cal_t defaults = SPIKE_CAL_DEFAULTS;
    rte_calpage_init(&s_calpage, &s_cal_page_a, &s_cal_page_b,
                     sizeof(spike_cal_t), &defaults);
    rte_daq_ring_init(&s_daq, s_daq_buf, sizeof s_daq_buf,
                      sizeof(spike_daq_frame_t));

    gpio_init(RTE_PIN_ISR_ACTIVE);
    gpio_set_dir(RTE_PIN_ISR_ACTIVE, GPIO_OUT);
    gpio_init(RTE_PIN_OVERRUN);
    gpio_set_dir(RTE_PIN_OVERRUN, GPIO_OUT);
}

static void __not_in_flash_func(rte_fast_tick_isr)(void) {
    const uint32_t t_entry = timer_hw->timerawl;
    hw_clear_bits(&timer_hw->intr, 1u << RTE_ALARM_NUM);
    gpio_put(RTE_PIN_ISR_ACTIVE, 1);

    if (fault_inject_isr_wedged()) {
        /* Injected wedge (BLD-007 drill): stop advancing the heartbeat while
         * core 1 keeps running — the cross-core monitor must catch it. */
        for (;;) {
        }
    }

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

    /* Step boundary: transactional CAL page switch (RTE-003). */
    rte_calpage_commit(&s_calpage);

    /* Copy-in (RTE-001): bounded seqlock read, stale fallback on failure. */
    if (!rte_seqlock_read(&s_sl_slow, &s_shadow_slow, &s_bus_slow,
                          sizeof s_shadow_slow)) {
        g_rte_telemetry.seqlock_fault_count++;
    }

    spike_fast_bus_t out;
    out.tick = g_rte_telemetry.fast_ticks;
    spike_fast_step((const spike_cal_t *) rte_calpage_active(&s_calpage),
                    &s_shadow_slow, &out);

    /* Publish (RTE-004) + coherent DAQ snapshot (RTE-005). */
    rte_seqlock_write(&s_sl_fast, &s_bus_fast, &out, sizeof out);
    const spike_daq_frame_t frame = {
        .tick = out.tick,
        .torque_cmd = out.torque_cmd,
        .iq_meas = out.iq_meas,
        .speed_est = s_speed_est,
    };
    rte_daq_ring_push(&s_daq, &frame);

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

/* --- Core 1 signal access ----------------------------------------------- */

void rte_fast_bus_read(spike_fast_bus_t *shadow) {
    if (!rte_seqlock_read(&s_sl_fast, shadow, &s_bus_fast, sizeof *shadow)) {
        g_rte_telemetry.seqlock_fault_count++;
    }
}

void rte_slow_bus_write(const spike_slow_bus_t *value) {
    rte_seqlock_write(&s_sl_slow, &s_bus_slow, value, sizeof *value);
}

void rte_speed_est_write(int32_t speed) {
    s_speed_est = speed;
}

/* --- DAQ ring ------------------------------------------------------------ */

bool rte_daq_pop_frame(spike_daq_frame_t *out) {
    if (rte_daq_ring_pop(&s_daq, out)) {
        g_rte_telemetry.daq_frames_drained++;
        return true;
    }
    return false;
}

uint32_t rte_daq_depth(void) {
    return rte_daq_ring_count(&s_daq);
}

uint32_t rte_daq_dropped(void) {
    return s_daq.dropped;
}

/* --- Calibration pages --------------------------------------------------- */

spike_cal_t *rte_cal_offline(void) {
    return (spike_cal_t *) rte_calpage_offline(&s_calpage);
}

void rte_cal_request_switch(void) {
    rte_calpage_request_switch(&s_calpage);
}

bool rte_cal_sync_offline(void) {
    return rte_calpage_sync_offline(&s_calpage);
}

void rte_cal_get_active(spike_cal_t *out) {
    *out = *(const spike_cal_t *) rte_calpage_active(&s_calpage);
}

uint32_t rte_cal_switch_count(void) {
    return s_calpage.switch_count;
}

uint32_t rte_cal_active_index(void) {
    return s_calpage.active_idx & 1u;
}

void *rte_cal_logical_base(void) {
    /* Fixed logical CAL window for the A2L: always page A's address,
     * independent of which page is currently active (RTE-003). */
    return &s_cal_page_a;
}
