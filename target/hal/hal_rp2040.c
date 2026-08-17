/* RP2040 implementation of the default HAL (GUI-006).
 *
 * The ISR-safe functions are __not_in_flash_func and touch only SIO/ADC/PWM
 * registers — no SDK calls that could block, allocate, or fetch from flash.
 */

#include "hal.h"

#include "hardware/adc.h"
#include "hardware/gpio.h"
#include "hardware/pwm.h"
#include "hardware/structs/sio.h"
#include "pico/platform.h"

#define HAL_PWM_WRAP 4095u /* ~30 kHz at 125 MHz sysclk */

/* GPIOs the default HAL claims for digital I/O and PWM demo channels. */
#define HAL_PIN_DIN 14
#define HAL_PIN_DOUT 15
#define HAL_PIN_PWM 16

void hal_init(void) {
    gpio_init(HAL_PIN_DIN);
    gpio_set_dir(HAL_PIN_DIN, GPIO_IN);
    gpio_pull_down(HAL_PIN_DIN);

    gpio_init(HAL_PIN_DOUT);
    gpio_set_dir(HAL_PIN_DOUT, GPIO_OUT);

    gpio_set_function(HAL_PIN_PWM, GPIO_FUNC_PWM);
    const uint slice = pwm_gpio_to_slice_num(HAL_PIN_PWM);
    pwm_set_wrap(slice, HAL_PWM_WRAP);
    pwm_set_enabled(slice, true);

    adc_init();
    adc_gpio_init(26); /* ADC0 */
    adc_gpio_init(27); /* ADC1 */
}

uint8_t __not_in_flash_func(hal_gpio_read)(uint8_t pin) {
    return (sio_hw->gpio_in >> pin) & 1u;
}

void __not_in_flash_func(hal_gpio_write)(uint8_t pin, uint8_t value) {
    if (value) {
        sio_hw->gpio_set = 1u << pin;
    } else {
        sio_hw->gpio_clr = 1u << pin;
    }
}

uint16_t __not_in_flash_func(hal_adc_read)(uint8_t channel) {
    /* Blocking single conversion: ~2 us at the default ADC clock — bounded
     * and budgeted as part of fast-loop execution time (BLD-003). */
    adc_hw->cs = (adc_hw->cs & ~ADC_CS_AINSEL_BITS)
                 | ((uint32_t) channel << ADC_CS_AINSEL_LSB) | ADC_CS_START_ONCE_BITS
                 | ADC_CS_EN_BITS;
    while (!(adc_hw->cs & ADC_CS_READY_BITS)) {
        tight_loop_contents();
    }
    return (uint16_t) adc_hw->result;
}

void __not_in_flash_func(hal_pwm_set_duty)(uint8_t gpio, uint16_t duty_u16) {
    /* Scale 0..65535 onto the wrap value; pure integer math (MAT-002). */
    const uint32_t level = ((uint32_t) duty_u16 * (HAL_PWM_WRAP + 1u)) >> 16;
    pwm_set_gpio_level(gpio, (uint16_t) level);
}
