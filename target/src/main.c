/* Phase 2 spike firmware: FreeRTOS SMP + the full RTE primitive set.
 *
 * Core 0: fast ISR (1 kHz) with copy-in, CAL commit, seqlock publish, DAQ
 * push. Core 1: rate tasks consuming the fast bus through their own seqlock
 * shadows, a DAQ drain task, and a stats task that exercises the CAL page
 * handshake and prints machine-parseable telemetry.
 */

#include <stdio.h>

#include "FreeRTOS.h"
#include "task.h"
#include "pico/stdlib.h"

#include "hal.h"
#include "rte.h"
#include "rte_sections.h"
#include "spike_models.h"

#define RATE_TASK_STACK_WORDS 512 /* 2 kB per task (BLD-005) */
#define CORE1_AFFINITY_MASK (1u << 1)

/* Task stacks + TCBs in SRAM1, core 1's bank (BLD-002). */
static StackType_t s_stack_rate10[RATE_TASK_STACK_WORDS] RTE_CORE1_BSS;
static StaticTask_t s_tcb_rate10 RTE_CORE1_BSS;
static StackType_t s_stack_rate100[RATE_TASK_STACK_WORDS] RTE_CORE1_BSS;
static StaticTask_t s_tcb_rate100 RTE_CORE1_BSS;
static StackType_t s_stack_xcp[RATE_TASK_STACK_WORDS] RTE_CORE1_BSS;
static StaticTask_t s_tcb_xcp RTE_CORE1_BSS;
static StackType_t s_stack_stats[RATE_TASK_STACK_WORDS] RTE_CORE1_BSS;
static StaticTask_t s_tcb_stats RTE_CORE1_BSS;

/* Per-task seqlock shadows (RTE-001), core 1's bank. */
static spike_fast_bus_t s_shadow_rate10 RTE_CORE1_BSS;
static spike_fast_bus_t s_shadow_rate100 RTE_CORE1_BSS;

static TaskHandle_t s_h_rate10;
static TaskHandle_t s_h_rate100;

static void rate10_task(void *arg) {
    (void) arg;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        rte_fast_bus_read(&s_shadow_rate10);
        rte_speed_est_write(spike_10ms_step(&s_shadow_rate10));
        g_rte_telemetry.rate10_activations++;
    }
}

static void rate100_task(void *arg) {
    (void) arg;
    spike_slow_bus_t slow;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        rte_fast_bus_read(&s_shadow_rate100);
        spike_100ms_step(&s_shadow_rate100, &slow);
        rte_slow_bus_write(&slow); /* single writer (GUI-009) */
        g_rte_telemetry.rate100_activations++;
    }
}

/* XCP slave task (target/xcp/xcp_cdc_task.c): CDC transport + DAQ drain. */
extern void xcp_task(void *arg);

static void stats_task(void *arg) {
    (void) arg;
    /* Registration enables ISR->task notifications only once the scheduler
     * is live on core 1 (the ISR skips NULL handles before this). */
    rte_dispatch_register(s_h_rate10, s_h_rate100);

    uint32_t last_hb = 0;
    uint32_t iteration = 0;
    bool cal_pending_sync = false;
    spike_cal_t cal_active;

    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        iteration++;

        /* Exercise the CAL page handshake end to end (RTE-003): edit the
         * offline page, arm the switch; the ISR commits at a step boundary;
         * re-base the new offline page on the next pass. */
        if (cal_pending_sync) {
            cal_pending_sync = !rte_cal_sync_offline();
        } else if ((iteration % 5u) == 0u) {
            spike_cal_t *offline = rte_cal_offline();
            offline->trq_limit = (offline->trq_limit > 24000) ? 20000 : 32767;
            offline->kp_q15 = (offline->kp_q15 == 9830) ? 11469 : 9830;
            rte_cal_request_switch();
            cal_pending_sync = true;
        }

        rte_cal_get_active(&cal_active);
        const uint32_t hb = g_rte_telemetry.heartbeat;
        /* Machine-parseable telemetry line (BLD-003); consumed by HIL scripts. */
        printf("RTE hb=%lu dhb=%lu exec_max=%lu jit_max=%lu ovr=%lu slf=%lu "
               "r10=%lu r100=%lu daq=%lu daq_depth=%lu daq_drop=%lu "
               "cal_sw=%lu cal_kp=%ld hwm10=%u hwm100=%u\n",
               (unsigned long) hb, (unsigned long) (hb - last_hb),
               (unsigned long) g_rte_telemetry.exec_max_us,
               (unsigned long) g_rte_telemetry.dispatch_jitter_max_us,
               (unsigned long) g_rte_telemetry.overrun_count,
               (unsigned long) g_rte_telemetry.seqlock_fault_count,
               (unsigned long) g_rte_telemetry.rate10_activations,
               (unsigned long) g_rte_telemetry.rate100_activations,
               (unsigned long) g_rte_telemetry.daq_frames_drained,
               (unsigned long) rte_daq_depth(),
               (unsigned long) rte_daq_dropped(),
               (unsigned long) rte_cal_switch_count(),
               (long) cal_active.kp_q15,
               (unsigned) uxTaskGetStackHighWaterMark(s_h_rate10),
               (unsigned) uxTaskGetStackHighWaterMark(s_h_rate100));
        last_hb = hb;
    }
}

int main(void) {
    stdio_init_all();
    printf("PicoDesk RTE spike boot\n");
    rte_init();
    hal_init();

    /* Rate-monotonic priorities, all pinned to core 1 (RTE-002, BLD-005). */
    s_h_rate10 = xTaskCreateStatic(rate10_task, "rate10", RATE_TASK_STACK_WORDS,
                                   NULL, 4, s_stack_rate10, &s_tcb_rate10);
    s_h_rate100 = xTaskCreateStatic(rate100_task, "rate100", RATE_TASK_STACK_WORDS,
                                    NULL, 3, s_stack_rate100, &s_tcb_rate100);
    TaskHandle_t h_stats = xTaskCreateStatic(stats_task, "stats", RATE_TASK_STACK_WORDS,
                                             NULL, 2, s_stack_stats, &s_tcb_stats);
    /* XCP at the lowest application priority (BLD-005). */
    TaskHandle_t h_xcp = xTaskCreateStatic(xcp_task, "xcp", RATE_TASK_STACK_WORDS,
                                           NULL, 1, s_stack_xcp, &s_tcb_xcp);
    vTaskCoreAffinitySet(s_h_rate10, CORE1_AFFINITY_MASK);
    vTaskCoreAffinitySet(s_h_rate100, CORE1_AFFINITY_MASK);
    vTaskCoreAffinitySet(h_xcp, CORE1_AFFINITY_MASK);
    vTaskCoreAffinitySet(h_stats, CORE1_AFFINITY_MASK);

    /* Arm the 1 kHz fast path on this core (core 0). Fires immediately; task
     * notification stays gated until stats_task registers the handles. */
    rte_dispatch_start(1000);

    vTaskStartScheduler(); /* must be called on core 0; port launches core 1 */
    for (;;) {
        /* unreachable */
    }
}

/* --- FreeRTOS static-allocation + safety hooks -------------------------- */

/* Assert record at a fixed address (start of .noinit_fault, SRAM2): readable
 * post-mortem from a debugger or the Renode monitor, survives resets. */
volatile uint32_t g_assert_info[3] RTE_NOINIT_FAULT; /* magic, file, line */

void vAssertCalled(const char *file, int line) {
    g_assert_info[0] = 0xDEAD5555u;
    g_assert_info[1] = (uint32_t) (uintptr_t) file;
    g_assert_info[2] = (uint32_t) line;
    __asm volatile("cpsid i");
    for (;;) {
    }
}

void vApplicationStackOverflowHook(TaskHandle_t task, char *name) {
    (void) task;
    (void) name;
    taskDISABLE_INTERRUPTS();
    for (;;) {
        __asm volatile("bkpt #0");
    }
}

void vApplicationMallocFailedHook(void) {
    taskDISABLE_INTERRUPTS();
    for (;;) {
        __asm volatile("bkpt #0");
    }
}

/* Idle/timer service tasks may run on either core, so their stacks live in
 * the shared bank rather than a core-owned one; their traffic is negligible. */
static StaticTask_t s_idle_tcb RTE_SHARED;
static StackType_t s_idle_stack[configMINIMAL_STACK_SIZE] RTE_SHARED;
static StaticTask_t s_passive_idle_tcb RTE_SHARED;
static StackType_t s_passive_idle_stack[configMINIMAL_STACK_SIZE] RTE_SHARED;

void vApplicationGetIdleTaskMemory(StaticTask_t **tcb, StackType_t **stack,
                                   configSTACK_DEPTH_TYPE *stack_size) {
    *tcb = &s_idle_tcb;
    *stack = s_idle_stack;
    *stack_size = configMINIMAL_STACK_SIZE;
}

void vApplicationGetPassiveIdleTaskMemory(StaticTask_t **tcb, StackType_t **stack,
                                          configSTACK_DEPTH_TYPE *stack_size,
                                          BaseType_t index) {
    (void) index; /* configNUMBER_OF_CORES == 2 -> exactly one passive idle */
    *tcb = &s_passive_idle_tcb;
    *stack = s_passive_idle_stack;
    *stack_size = configMINIMAL_STACK_SIZE;
}

static StaticTask_t s_timer_tcb RTE_SHARED;
static StackType_t s_timer_stack[configTIMER_TASK_STACK_DEPTH] RTE_SHARED;

void vApplicationGetTimerTaskMemory(StaticTask_t **tcb, StackType_t **stack,
                                    configSTACK_DEPTH_TYPE *stack_size) {
    *tcb = &s_timer_tcb;
    *stack = s_timer_stack;
    *stack_size = configTIMER_TASK_STACK_DEPTH;
}
