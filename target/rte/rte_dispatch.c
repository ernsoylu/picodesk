/* Multi-rate dispatcher (RTE-002).
 *
 * The fastest rate group runs in the Core 0 hardware timer alarm ISR; slower
 * rate groups are FreeRTOS tasks on Core 1 released via
 * vTaskNotifyGiveFromISR(). The ISR, the fast model_step() calls, and the
 * copy-in routines (RTE-001) must all execute from SRAM via
 * __not_in_flash_func() (BLD-001) and contain no floating point (MAT-002 —
 * the RP2040 has no FPU). CAL page switches commit only here, at the step
 * boundary (RTE-003).
 */

#include "rte.h"

/* TODO: __not_in_flash_func(rte_fast_step_isr) — copy-in, model_step calls,
 * DAQ snapshot push, telemetry, heartbeat, calpage commit, task notify. */
