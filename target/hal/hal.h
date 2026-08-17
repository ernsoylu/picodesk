/* Default HAL: GPIO, ADC, PWM (GUI-006).
 *
 * Functions flagged isr_safe in hal_manifest.json may be bound to fast-loop
 * models and therefore run inside the core 0 timer ISR: register access only,
 * non-blocking, float-free, SRAM-resident (BLD-001, MAT-002, NFR-3).
 * hal_init() runs once at boot on core 0 before the scheduler starts and is
 * the only function here allowed to touch flash-resident SDK code.
 */

#ifndef PICODESK_HAL_H
#define PICODESK_HAL_H

#include <stdint.h>

void hal_init(void);

/* ISR-safe (manifest: isr_safe=true). */
uint8_t hal_gpio_read(uint8_t pin);
void hal_gpio_write(uint8_t pin, uint8_t value);
uint16_t hal_adc_read(uint8_t channel);
void hal_pwm_set_duty(uint8_t gpio, uint16_t duty_u16);

#endif /* PICODESK_HAL_H */
