/* Bank placement attributes matching ld/rp2040_banked.ld (BLD-002).
 *
 * All three sections are NOLOAD: crt0 does not zero them. rte_init() clears
 * them explicitly at boot (RTE-004). .noinit_fault (BLD-006) is deliberately
 * NOT covered here — it must survive resets and is never cleared.
 */

#ifndef PICODESK_RTE_SECTIONS_H
#define PICODESK_RTE_SECTIONS_H

/* SRAM2: cross-core RTE data — seqlocks, DAQ ring, CAL pages, telemetry,
 * FreeRTOS heap. */
#define RTE_SHARED __attribute__( ( section( ".rte_shared" ) ) )

/* SRAM0: data owned by the core 0 fast path (ISR-local state). */
#define RTE_CORE0_BSS __attribute__( ( section( ".core0_bss" ) ) )

/* SRAM1: data owned by core 1 (FreeRTOS task stacks, TCBs). */
#define RTE_CORE1_BSS __attribute__( ( section( ".core1_bss" ) ) )

/* SRAM2, never cleared: HardFault record storage (BLD-006). */
#define RTE_NOINIT_FAULT __attribute__( ( section( ".noinit_fault" ) ) )

#endif /* PICODESK_RTE_SECTIONS_H */
