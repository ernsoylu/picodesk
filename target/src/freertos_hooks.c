/* FreeRTOS static-allocation + safety hooks, shared by the spike firmware
 * and generated firmware executables.
 *
 * The assert record lands at the start of .noinit_fault (SRAM2) so it is
 * readable post-mortem from a debugger or the Renode monitor and survives
 * watchdog resets (BLD-006 groundwork).
 */

#include <stdint.h>

#include "FreeRTOS.h"
#include "task.h"

#include "rte_sections.h"

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
