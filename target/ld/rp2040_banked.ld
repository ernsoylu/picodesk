/* Bank-separated RP2040 linker script (BLD-002), derived from Pico SDK 1.5.1
 * memmap_default.ld.
 *
 * The 4 x 64 kB main SRAM banks are addressed through their NON-striped
 * aliases so each bank has a single deterministic owner and the cores never
 * contend on a bank in steady state:
 *
 *   SRAM0 0x21000000  Core 0: main/ISR stack (fast path) + .core0_bss
 *   SRAM1 0x21010000  Core 1: boot stack + .core1_bss (FreeRTOS task stacks/TCBs)
 *   SRAM2 0x21020000  RTE shared data: .rte_shared (seqlocks, DAQ ring, CAL
 *                     pages, telemetry, FreeRTOS heap) + .noinit_fault (BLD-006)
 *   SRAM3 0x21030000  "RAM": SDK .data/.bss/vector table and all SRAM-executed
 *                     code (.time_critical from __not_in_flash_func, BLD-001)
 *
 * The striped window 0x20000000-0x2003FFFF aliases the same physical memory
 * and is deliberately NOT a region here, so nothing can be placed through it.
 *
 * .core0_bss/.core1_bss/.rte_shared are NOLOAD and are NOT zeroed by crt0 —
 * rte_init() clears them explicitly (RTE-004 boot defaults). .noinit_fault is
 * never cleared so a HardFault record survives watchdog resets (BLD-006).
 */

MEMORY
{
    FLASH(rx) : ORIGIN = 0x10000000, LENGTH = 2048k
    SRAM0(rwx) : ORIGIN = 0x21000000, LENGTH = 64k
    SRAM1(rwx) : ORIGIN = 0x21010000, LENGTH = 64k
    SRAM2(rwx) : ORIGIN = 0x21020000, LENGTH = 64k
    RAM(rwx)   : ORIGIN = 0x21030000, LENGTH = 64k
    SCRATCH_X(rwx) : ORIGIN = 0x20040000, LENGTH = 4k
    SCRATCH_Y(rwx) : ORIGIN = 0x20041000, LENGTH = 4k
}

ENTRY(_entry_point)

SECTIONS
{
    .flash_begin : {
        __flash_binary_start = .;
    } > FLASH

    .boot2 : {
        __boot2_start__ = .;
        KEEP (*(.boot2))
        __boot2_end__ = .;
    } > FLASH

    ASSERT(__boot2_end__ - __boot2_start__ == 256,
        "ERROR: Pico second stage bootloader must be 256 bytes in size")

    .text : {
        __logical_binary_start = .;
        KEEP (*(.vectors))
        KEEP (*(.binary_info_header))
        __binary_info_header_end = .;
        KEEP (*(.reset))
        *(.init)
        *(EXCLUDE_FILE(*libgcc.a: *libc.a:*lib_a-mem*.o *libm.a:) .text*)
        *(.fini)
        *crtbegin.o(.ctors)
        *crtbegin?.o(.ctors)
        *(EXCLUDE_FILE(*crtend?.o *crtend.o) .ctors)
        *(SORT(.ctors.*))
        *(.ctors)
        *crtbegin.o(.dtors)
        *crtbegin?.o(.dtors)
        *(EXCLUDE_FILE(*crtend?.o *crtend.o) .dtors)
        *(SORT(.dtors.*))
        *(.dtors)
        *(.eh_frame*)
        . = ALIGN(4);
    } > FLASH

    .rodata : {
        *(EXCLUDE_FILE(*libgcc.a: *libc.a:*lib_a-mem*.o *libm.a:) .rodata*)
        . = ALIGN(4);
        *(SORT_BY_ALIGNMENT(SORT_BY_NAME(.flashdata*)))
        . = ALIGN(4);
    } > FLASH

    .ARM.extab :
    {
        *(.ARM.extab* .gnu.linkonce.armextab.*)
    } > FLASH

    __exidx_start = .;
    .ARM.exidx :
    {
        *(.ARM.exidx* .gnu.linkonce.armexidx.*)
    } > FLASH
    __exidx_end = .;

    . = ALIGN(4);
    __binary_info_start = .;
    .binary_info :
    {
        KEEP(*(.binary_info.keep.*))
        *(.binary_info.*)
    } > FLASH
    __binary_info_end = .;
    . = ALIGN(4);

   .ram_vector_table (NOLOAD): {
        *(.ram_vector_table)
    } > RAM

    .data : {
        __data_start__ = .;
        *(vtable)

        *(.time_critical*)

        /* remaining .text and .rodata excluded from FLASH above -> SRAM3 */
        *(.text*)
        . = ALIGN(4);
        *(.rodata*)
        . = ALIGN(4);

        *(.data*)

        . = ALIGN(4);
        *(.after_data.*)
        . = ALIGN(4);
        PROVIDE_HIDDEN (__mutex_array_start = .);
        KEEP(*(SORT(.mutex_array.*)))
        KEEP(*(.mutex_array))
        PROVIDE_HIDDEN (__mutex_array_end = .);

        . = ALIGN(4);
        PROVIDE_HIDDEN (__preinit_array_start = .);
        KEEP(*(SORT(.preinit_array.*)))
        KEEP(*(.preinit_array))
        PROVIDE_HIDDEN (__preinit_array_end = .);

        . = ALIGN(4);
        PROVIDE_HIDDEN (__init_array_start = .);
        KEEP(*(SORT(.init_array.*)))
        KEEP(*(.init_array))
        PROVIDE_HIDDEN (__init_array_end = .);

        . = ALIGN(4);
        PROVIDE_HIDDEN (__fini_array_start = .);
        *(SORT(.fini_array.*))
        *(.fini_array)
        PROVIDE_HIDDEN (__fini_array_end = .);

        *(.jcr)
        . = ALIGN(4);
        __data_end__ = .;
    } > RAM AT> FLASH
    __etext = LOADADDR(.data);

    .uninitialized_data (NOLOAD): {
        . = ALIGN(4);
        *(.uninitialized_data*)
    } > RAM

    .scratch_x : {
        __scratch_x_start__ = .;
        *(.scratch_x.*)
        . = ALIGN(4);
        __scratch_x_end__ = .;
    } > SCRATCH_X AT > FLASH
    __scratch_x_source__ = LOADADDR(.scratch_x);

    .scratch_y : {
        __scratch_y_start__ = .;
        *(.scratch_y.*)
        . = ALIGN(4);
        __scratch_y_end__ = .;
    } > SCRATCH_Y AT > FLASH
    __scratch_y_source__ = LOADADDR(.scratch_y);

    .bss  : {
        . = ALIGN(4);
        __bss_start__ = .;
        *(SORT_BY_ALIGNMENT(SORT_BY_NAME(.bss*)))
        *(COMMON)
        . = ALIGN(4);
        __bss_end__ = .;
    } > RAM

    .heap (NOLOAD):
    {
        __end__ = .;
        end = __end__;
        KEEP(*(.heap*))
        __HeapLimit = .;
    } > RAM

    /* ---- PicoDesk bank-owned sections (BLD-002) ---- */

    .core0_bss (NOLOAD): {
        . = ALIGN(4);
        __core0_bss_start__ = .;
        *(.core0_bss*)
        . = ALIGN(4);
        __core0_bss_end__ = .;
    } > SRAM0

    .core1_bss (NOLOAD): {
        . = ALIGN(4);
        __core1_bss_start__ = .;
        *(.core1_bss*)
        . = ALIGN(4);
        __core1_bss_end__ = .;
    } > SRAM1

    /* Fault record first at a fixed address, never cleared (BLD-006). */
    .noinit_fault (NOLOAD): {
        . = ALIGN(4);
        __noinit_fault_start__ = .;
        KEEP(*(.noinit_fault*))
        . = ALIGN(4);
        __noinit_fault_end__ = .;
    } > SRAM2

    .rte_shared (NOLOAD): {
        . = ALIGN(4);
        __rte_shared_start__ = .;
        *(.rte_shared*)
        . = ALIGN(4);
        __rte_shared_end__ = .;
    } > SRAM2

    /* Sized like the SDK dummies: only measure, placement is via symbols below. */
    .stack1_dummy (NOLOAD):
    {
        *(.stack1*)
    } > SRAM1
    .stack_dummy (NOLOAD):
    {
        KEEP(*(.stack*))
    } > SRAM0

    .flash_end : {
        PROVIDE(__flash_binary_end = .);
    } > FLASH

    /* Heap limit for newlib malloc stays inside SRAM3. */
    __StackLimit = ORIGIN(RAM) + LENGTH(RAM);

    /* Core 0 main/ISR stack at the top of SRAM0; core 1 boot stack at the top
     * of SRAM1 (FreeRTOS task stacks are static arrays in .core1_bss). */
    __StackTop = ORIGIN(SRAM0) + LENGTH(SRAM0);
    __StackBottom = __StackTop - SIZEOF(.stack_dummy);
    __StackOneTop = ORIGIN(SRAM1) + LENGTH(SRAM1);
    __StackOneBottom = __StackOneTop - SIZEOF(.stack1_dummy);
    PROVIDE(__stack = __StackTop);

    ASSERT(__StackLimit >= __HeapLimit, "region RAM overflowed")
    ASSERT(__core0_bss_end__ <= __StackBottom, "core0 bss collides with core 0 stack")
    ASSERT(__core1_bss_end__ <= __StackOneBottom, "core1 bss collides with core 1 stack")
    ASSERT(__rte_shared_end__ <= ORIGIN(SRAM2) + LENGTH(SRAM2), "SRAM2 shared bank overflowed")

    ASSERT( __binary_info_header_end - __logical_binary_start <= 256, "Binary info must be in first 256 bytes of the binary")
}
