/* Bank-separated RP2040 linker script (BLD-002) — skeleton.
 *
 * Start from the Pico SDK's memmap_default.ld and split the 264 kB SRAM so the
 * two cores never contend on a bank in the steady state:
 *
 *   SRAM0 (64 kB striped -> use banks 0-1 as dedicated): Core 0 stack + BSS
 *   SRAM1: Core 1 stack + BSS (FreeRTOS task stacks)
 *   SRAM2: RTE shared data — seqlocks, DAQ ring, CAL pages, FreeRTOS heap
 *
 * Also required here:
 *   - RAM_SHARED region + section for A2L segment addressing (MAT-003)
 *   - .noinit_fault section, excluded from BSS zeroing, for the HardFault
 *     record that must survive watchdog reset (BLD-006)
 *   - SRAM placement of __not_in_flash_func code (BLD-001, SDK sections)
 *
 * TODO: copy memmap_default.ld from the SDK and apply the layout above.
 */
