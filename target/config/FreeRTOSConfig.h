/* FreeRTOS SMP configuration — skeleton.
 *
 * Kernel v11.1.0+ SMP port, both cores in the scheduler
 * (configNUMBER_OF_CORES = 2). The Core 0 fast path is a hardware timer IRQ
 * above the kernel, not a task. Constraints this file must uphold:
 *   - Kernel critical sections bounded by the 15 us budget on every core
 *     (NFR-3) — keep configMAX_SYSCALL... / spinlock usage auditable.
 *   - Task stacks default to 2 kB; high-water marks telemetered (BLD-005):
 *     configUSE_TRACE_FACILITY / uxTaskGetStackHighWaterMark enabled.
 *   - FreeRTOS heap placed in SRAM2 (BLD-002).
 */

#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#define configNUMBER_OF_CORES 2

/* TODO: full configuration against the FreeRTOS SMP RP2040 port. */

#endif /* FREERTOS_CONFIG_H */
