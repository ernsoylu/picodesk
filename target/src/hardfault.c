/* HardFault capture (BLD-006).
 *
 * The handler stores the stacked PC/LR (plus fault status) into a reserved
 * uninitialized RAM section so the record survives the watchdog reset and can
 * be printed on the next boot. The section must be excluded from BSS zeroing
 * in ld/rp2040_banked.ld and validated with a magic word before printout.
 */

/* TODO: __attribute__((section(".noinit_fault"))) record + naked handler. */
