/* RTE core API and execution-health telemetry.
 *
 * Telemetry (BLD-003) is measured with the RP2040 64-bit microsecond timer;
 * counters live in SRAM2 shared data (BLD-002) and are exposed as XCP
 * measurements. The heartbeat feeds the cross-core watchdog (BLD-007): Core 0
 * increments it every fast-loop tick, the Core 1 watchdog task verifies
 * advancement before feeding the hardware watchdog.
 */

#ifndef PICODESK_RTE_H
#define PICODESK_RTE_H

#include <stdint.h>

typedef struct {
    uint32_t isr_exec_time_us_max;
    uint32_t isr_overrun_count;
    uint32_t seqlock_fault_count;
    volatile uint32_t core0_heartbeat;
} rte_telemetry_t;

void rte_init(void);

#endif /* PICODESK_RTE_H */
