/* Phase 0 walking skeleton: prove the pinned toolchain, SDK, and reproducible
 * build pipeline end to end with the simplest possible firmware. Replaced by
 * the FreeRTOS SMP real-time spike in Phase 1.
 */

#include "pico/stdlib.h"

#ifndef PICO_DEFAULT_LED_PIN
#define PICO_DEFAULT_LED_PIN 25
#endif

int main(void) {
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
    for (;;) {
        gpio_xor_mask(1u << PICO_DEFAULT_LED_PIN);
        sleep_ms(250);
    }
}
