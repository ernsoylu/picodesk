cmake_minimum_required(VERSION 3.20)

# Pico SDK >= 1.5.1 (GUI-004). Defaults to the pinned submodule; PICO_SDK_PATH
# (cache or env) overrides for out-of-tree SDKs.
if(NOT DEFINED PICO_SDK_PATH)
    if(DEFINED ENV{PICO_SDK_PATH})
        set(PICO_SDK_PATH $ENV{PICO_SDK_PATH})
    else()
        set(PICO_SDK_PATH ${CMAKE_CURRENT_SOURCE_DIR}/../external/pico-sdk)
    endif()
endif()
get_filename_component(PICO_SDK_PATH "${PICO_SDK_PATH}" REALPATH)
include(${PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(picodesk_firmware C CXX ASM)
set(CMAKE_C_STANDARD 11)

pico_sdk_init()

# FreeRTOS-Kernel >= 11.1.0, SMP RP2040 port (SRS section 8), pinned submodule.
if(NOT DEFINED FREERTOS_KERNEL_PATH)
    set(FREERTOS_KERNEL_PATH ${CMAKE_CURRENT_SOURCE_DIR}/../external/FreeRTOS-Kernel)
endif()
get_filename_component(FREERTOS_KERNEL_PATH "${FREERTOS_KERNEL_PATH}" REALPATH)
include(${FREERTOS_KERNEL_PATH}/portable/ThirdParty/GCC/RP2040/FreeRTOS_Kernel_import.cmake)

# Reproducible builds (BLD-008): identical inputs must yield an identical .uf2.
# Path prefixes are normalized for every object (SDK included) so debug info and
# __FILE__ never leak absolute paths; PICO_PROGRAM_BUILD_DATE preempts the SDK's
# __DATE__ use in binary_info.c.
add_compile_options(
    -ffile-prefix-map=${CMAKE_CURRENT_SOURCE_DIR}=SRC
    -ffile-prefix-map=${PICO_SDK_PATH}=PICO_SDK
    -ffile-prefix-map=${FREERTOS_KERNEL_PATH}=FREERTOS
    -ffile-prefix-map=${CMAKE_BINARY_DIR}=BUILD
    # CAL-002: the ELF must carry DWARF so A2L addresses (including inner
    # members of nested structs) can be resolved. Debug info never reaches
    # the .uf2 — only loadable sections do — so this costs no flash and,
    # with the prefix maps above, does not disturb reproducibility.
    -g
)
add_compile_definitions(PICO_PROGRAM_BUILD_DATE="reproducible")

# Renode simulation build (sim/): USB is not modeled, so the XCP task drops
# its TinyUSB transport and only drains the DAQ ring. Everything else —
# scheduler, fast ISR, seqlocks, CAL pages, banked memory — is identical.
option(PICODESK_SIM "Build for Renode simulation (no USB/TinyUSB)" OFF)

set(PICODESK_SOURCES
    src/main.c
    src/freertos_hooks.c
    src/spike_models.c
    src/hardfault.c
    src/fault_inject.c
    rte/rte_watchdog.c
    rte/rte_dispatch.c
    rte/rte_seqlock.c
    rte/rte_daq_ring.c
    rte/rte_calpage.c
    hal/hal_rp2040.c
    xcp/xcp_core.c
    xcp/xcp_cdc_task.c
)
if(NOT PICODESK_SIM)
    list(APPEND PICODESK_SOURCES xcp/usb_descriptors.c)
endif()

add_executable(picodesk_firmware ${PICODESK_SOURCES})

target_include_directories(picodesk_firmware PRIVATE rte config src hal xcp)

target_compile_options(picodesk_firmware PRIVATE -Wall -Werror=date-time)

target_link_libraries(picodesk_firmware
    pico_stdlib
    pico_multicore
    hardware_timer
    hardware_irq
    hardware_gpio
    hardware_adc
    hardware_pwm
    hardware_watchdog
    FreeRTOS-Kernel
    FreeRTOS-Kernel-Heap4
)

if(PICODESK_SIM)
    target_compile_definitions(picodesk_firmware PRIVATE PICODESK_SIM=1)
else()
    target_link_libraries(picodesk_firmware tinyusb_device tinyusb_board)
endif()

# ---- Generated firmware (Phase 5, milestone M1) -----------------------------
# PICODESK_GEN_DIR points at a picodesk.rtegen output directory; model step
# implementations (ERT output or stand-ins) live in its models/ subdirectory.
set(PICODESK_GEN_DIR "" CACHE PATH "Directory with generated RTE sources")
if(PICODESK_GEN_DIR)
    # models/fast and models/slow hold ERT output plus its adapter; the
    # split is load-bearing — the linker script places */fast/* in SRAM
    # (BLD-001). models/ alone is the stand-in layout.
    file(GLOB PICODESK_GEN_SOURCES
        ${PICODESK_GEN_DIR}/*.c
        ${PICODESK_GEN_DIR}/models/*.c
        ${PICODESK_GEN_DIR}/models/fast/*.c
        ${PICODESK_GEN_DIR}/models/slow/*.c)
    add_executable(picodesk_gen_firmware
        src/main_gen.c
        src/freertos_hooks.c
        src/hardfault.c
        src/fault_inject.c
        rte/rte_watchdog.c
        rte/rte_seqlock.c
        rte/rte_daq_ring.c
        rte/rte_calpage.c
        hal/hal_rp2040.c
        ${PICODESK_GEN_SOURCES}
    )
    target_include_directories(picodesk_gen_firmware PRIVATE
        rte config src hal
        ${PICODESK_GEN_DIR}
        ${PICODESK_GEN_DIR}/models/include
        ${PICODESK_GEN_DIR}/models/fast
        ${PICODESK_GEN_DIR}/models/slow)
    target_compile_options(picodesk_gen_firmware PRIVATE -Wall -Werror=date-time)
    target_link_libraries(picodesk_gen_firmware
        pico_stdlib
        pico_multicore
        hardware_timer
        hardware_irq
        hardware_gpio
        hardware_adc
        hardware_pwm
        hardware_watchdog
        FreeRTOS-Kernel
        FreeRTOS-Kernel-Heap4
    )
    pico_enable_stdio_uart(picodesk_gen_firmware 1)
    pico_enable_stdio_usb(picodesk_gen_firmware 0)
    pico_set_linker_script(picodesk_gen_firmware
        ${CMAKE_CURRENT_SOURCE_DIR}/ld/rp2040_banked.ld)
    pico_add_extra_outputs(picodesk_gen_firmware)
endif()

# Telemetry over UART; USB CDC arrives with XCP in Phase 3.
pico_enable_stdio_uart(picodesk_firmware 1)
pico_enable_stdio_usb(picodesk_firmware 0)

# Bank-separated memory layout (BLD-002); audited by tools/check_memmap.py.
pico_set_linker_script(picodesk_firmware ${CMAKE_CURRENT_SOURCE_DIR}/ld/rp2040_banked.ld)

pico_add_extra_outputs(picodesk_firmware)
