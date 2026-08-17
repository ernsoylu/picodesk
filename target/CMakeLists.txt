cmake_minimum_required(VERSION 3.20)

# PICO_SDK_PATH must point at Pico SDK >= 1.5.1 (verified host-side, GUI-004).
include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(picodesk_firmware C CXX ASM)
set(CMAKE_C_STANDARD 11)

pico_sdk_init()

# Reproducible builds (BLD-008): identical inputs must yield an identical .uf2.
# -Werror=date-time bans __DATE__/__TIME__; the prefix map strips absolute paths.
add_compile_options(
    -Wall
    -Werror=date-time
    -ffile-prefix-map=${CMAKE_SOURCE_DIR}=.
)

add_executable(picodesk_firmware
    src/main.c
    src/hardfault.c
    rte/rte_dispatch.c
    # TODO: generated RTE sources (added by the host toolchain into generated/),
    # XCPlite sources + CDC transport shim, HAL implementation files.
)

target_include_directories(picodesk_firmware PRIVATE
    rte
    hal
    config
)

# TODO: add FreeRTOS-Kernel (SMP, >= 11.1.0) via FetchContent or submodule,
# with configNUMBER_OF_CORES=2 from config/FreeRTOSConfig.h.

# Bank-separated memory layout (BLD-002): SRAM0=core0, SRAM1=core1, SRAM2=shared.
pico_set_linker_script(picodesk_firmware ${CMAKE_SOURCE_DIR}/ld/rp2040_banked.ld)

target_link_libraries(picodesk_firmware
    pico_stdlib
    pico_multicore
    hardware_timer
    hardware_watchdog
)

# Produces the .uf2 whose SHA-256 the host verifies for reproducibility (BLD-008).
pico_add_extra_outputs(picodesk_firmware)
