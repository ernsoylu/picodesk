/* Default HAL: GPIO, ADC, PWM (GUI-006).
 *
 * Functions flagged isr_safe in hal_manifest.json may be bound to fast-loop
 * models and therefore run inside the Core 0 timer ISR: they must be
 * non-blocking, float-free, and execute from SRAM (BLD-001, MAT-002, NFR-3).
 */

#ifndef PICODESK_HAL_H
#define PICODESK_HAL_H

#include <stdint.h>

void hal_init(void);

uint8_t hal_gpio_read(uint8_t pin);
void hal_gpio_write(uint8_t pin, uint8_t value);
uint16_t hal_adc_read(uint8_t channel);
void hal_pwm_set_duty(uint8_t slice, uint16_t duty);

#endif /* PICODESK_HAL_H */
