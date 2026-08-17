/* Phase 1 real-time spike firmware.
 *
 * Boot on core 0: zero RTE sections (RTE-004), create core-1-pinned rate
 * tasks (RTE-002, rate-monotonic priorities per BLD-005), arm the fast timer
 * ISR, then start the SMP scheduler — the RP2040 port launches core 1 itself
 * and core 0 drops into the idle task under the fast ISR.
 */

#include <stdio.h>

#include "FreeRTOS.h"
#include "task.h"
#include "pico/stdlib.h"

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
static StackType_t s_stack_stats[RATE_TASK_STACK_WORDS] RTE_CORE1_BSS;
static StaticTask_t s_tcb_stats RTE_CORE1_BSS;

static TaskHandle_t s_h_rate10;
static TaskHandle_t s_h_rate100;

static void rate10_task(void *arg) {
    (void) arg;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        spike_10ms_step();
        g_rte_telemetry.rate10_activations++;
    }
}

static void rate100_task(void *arg) {
    (void) arg;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        spike_100ms_step();
        g_rte_telemetry.rate100_activations++;
    }
}

static void stats_task(void *arg) {
    (void) arg;
    /* Registration enables ISR->task notifications only once the scheduler
     * is live on core 1 (the ISR skips NULL handles before this). */
    rte_dispatch_register(s_h_rate10, s_h_rate100);

    uint32_t last_hb = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        const uint32_t hb = g_rte_telemetry.heartbeat;
        /* Machine-parseable telemetry line (BLD-003); consumed by HIL scripts. */
        printf("RTE hb=%lu dhb=%lu exec_last=%lu exec_max=%lu jit_max=%lu ovr=%lu "
               "r10=%lu r100=%lu hwm10=%u hwm100=%u\n",
               (unsigned long) hb, (unsigned long) (hb - last_hb),
               (unsigned long) g_rte_telemetry.exec_last_us,
               (unsigned long) g_rte_telemetry.exec_max_us,
               (unsigned long) g_rte_telemetry.dispatch_jitter_max_us,
               (unsigned long) g_rte_telemetry.overrun_count,
               (unsigned long) g_rte_telemetry.rate10_activations,
               (unsigned long) g_rte_telemetry.rate100_activations,
               (unsigned) uxTaskGetStackHighWaterMark(s_h_rate10),
               (unsigned) uxTaskGetStackHighWaterMark(s_h_rate100));
        last_hb = hb;
    }
}

int main(void) {
    stdio_init_all();
    rte_init();

    /* Rate-monotonic priorities, both pinned to core 1 (RTE-002, BLD-005). */
    s_h_rate10 = xTaskCreateStatic(rate10_task, "rate10", RATE_TASK_STACK_WORDS,
                                   NULL, 4, s_stack_rate10, &s_tcb_rate10);
    s_h_rate100 = xTaskCreateStatic(rate100_task, "rate100", RATE_TASK_STACK_WORDS,
                                    NULL, 3, s_stack_rate100, &s_tcb_rate100);
    TaskHandle_t h_stats = xTaskCreateStatic(stats_task, "stats", RATE_TASK_STACK_WORDS,
                                             NULL, 1, s_stack_stats, &s_tcb_stats);
    vTaskCoreAffinitySet(s_h_rate10, CORE1_AFFINITY_MASK);
    vTaskCoreAffinitySet(s_h_rate100, CORE1_AFFINITY_MASK);
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

void vAssertCalled(const char *file, int line) {
    (void) file;
    (void) line;
    __asm volatile("cpsid i");
    for (;;) {
        __asm volatile("bkpt #0");
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

/* Idle tasks may run on either core, so their stacks live in the shared bank
 * rather than a core-owned one; idle stack traffic is negligible. */
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
