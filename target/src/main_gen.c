/* Entry point for GENERATED firmware (Phase 5, milestone M1).
 *
 * Everything model- and routing-specific lives in the generated rte_gen.c;
 * this main is workspace-independent: boot, init, start, schedule.
 */

#include <stdio.h>

#include "FreeRTOS.h"
#include "task.h"
#include "pico/stdlib.h"

#include "hal.h"
#include "rte_gen.h"

int main(void) {
    stdio_init_all();
    printf("PicoDesk generated RTE boot\n");
    rte_gen_init();
    hal_init();
    rte_gen_start();
    vTaskStartScheduler(); /* core 0; the SMP port launches core 1 */
    for (;;) {
        /* unreachable */
    }
}
