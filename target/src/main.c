/* Firmware entry point.
 *
 * Boot sequence: print any captured HardFault record from the previous run
 * (BLD-006), initialize the HAL, initialize all routed signals to
 * zero/producer defaults (RTE-004), create the Core 1 slow-path tasks and
 * watchdog task (RTE-002, BLD-007), arm the Core 0 fast-path timer alarm,
 * then start the FreeRTOS SMP scheduler.
 */

int main(void) {
    /* TODO */
    for (;;) {
    }
}
