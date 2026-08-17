/* FreeRTOS SMP configuration for the RP2040 port (V11.1.0).
 *
 * Architecture (SRS 1.2): both cores run the SMP scheduler, but core 0 hosts
 * only the idle task — the fast path is a hardware timer IRQ above the kernel.
 * configTICK_CORE = 1 keeps the SysTick tick interrupt off core 0 so the
 * kernel timekeeping never adds jitter to the fast loop (NFR-1).
 *
 * Memory (BLD-002): the kernel heap is application-allocated (ucHeap lives in
 * .rte_shared / SRAM2, defined in rte_dispatch.c); task stacks and TCBs are
 * static allocations placed in .core1_bss / SRAM1 by main.c.
 */

#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

/* --- Scheduler ---------------------------------------------------------- */
#define configUSE_PREEMPTION                    1
#define configUSE_TIME_SLICING                  1
#define configUSE_IDLE_HOOK                     0
#define configUSE_PASSIVE_IDLE_HOOK             0
#define configUSE_TICK_HOOK                     0
#define configTICK_RATE_HZ                      1000
#define configMAX_PRIORITIES                    8
#define configMINIMAL_STACK_SIZE                256
#define configTICK_TYPE_WIDTH_IN_BITS           TICK_TYPE_WIDTH_32_BITS
#define configIDLE_SHOULD_YIELD                 1
#define configUSE_TASK_NOTIFICATIONS            1
#define configTASK_NOTIFICATION_ARRAY_ENTRIES   1
#define configUSE_MUTEXES                       1
#define configUSE_RECURSIVE_MUTEXES             1
#define configUSE_COUNTING_SEMAPHORES           1
#define configQUEUE_REGISTRY_SIZE               8
#define configENABLE_BACKWARD_COMPATIBILITY     0
#define configNUM_THREAD_LOCAL_STORAGE_POINTERS 5
#define configMAX_TASK_NAME_LEN                 12

/* --- SMP (RP2040) ------------------------------------------------------- */
#define configNUMBER_OF_CORES                   2
#define configTICK_CORE                         1
#define configRUN_MULTIPLE_PRIORITIES           1
#define configUSE_CORE_AFFINITY                 1
#define configUSE_TASK_PREEMPTION_DISABLE       0
#define configSUPPORT_PICO_SYNC_INTEROP         1
#define configSUPPORT_PICO_TIME_INTEROP         1

/* --- Memory ------------------------------------------------------------- */
#define configSUPPORT_STATIC_ALLOCATION         1
#define configSUPPORT_DYNAMIC_ALLOCATION        1
#define configAPPLICATION_ALLOCATED_HEAP        1
#define configTOTAL_HEAP_SIZE                   (16 * 1024)
#define configCHECK_FOR_STACK_OVERFLOW          2
#define configUSE_MALLOC_FAILED_HOOK            1

/* --- Diagnostics (BLD-003 / BLD-005) ------------------------------------ */
#define configUSE_TRACE_FACILITY                1
#define configGENERATE_RUN_TIME_STATS           0

/* --- Software timers ---------------------------------------------------- */
/* Required: configSUPPORT_PICO_SYNC_INTEROP defers cross-core wakeups through
 * xEventGroupSetBitsFromISR, which needs the timer service task. */
#define configUSE_TIMERS                        1
#define configTIMER_TASK_PRIORITY               ( configMAX_PRIORITIES - 1 )
#define configTIMER_QUEUE_LENGTH                8
#define configTIMER_TASK_STACK_DEPTH            256
#define INCLUDE_xTimerPendFunctionCall          1

extern void vAssertCalled( const char * file, int line );
#define configASSERT( x ) if( ( x ) == 0 ) vAssertCalled( __FILE__, __LINE__ )

/* --- API inclusions ----------------------------------------------------- */
#define INCLUDE_vTaskPrioritySet                1
#define INCLUDE_uxTaskPriorityGet               1
#define INCLUDE_vTaskDelete                     0
#define INCLUDE_vTaskSuspend                    1
#define INCLUDE_xTaskDelayUntil                 1
#define INCLUDE_vTaskDelay                      1
#define INCLUDE_xTaskGetSchedulerState          1
#define INCLUDE_xTaskGetCurrentTaskHandle       1
#define INCLUDE_uxTaskGetStackHighWaterMark     1

#endif /* FREERTOS_CONFIG_H */
