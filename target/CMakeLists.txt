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
)
add_compile_definitions(PICO_PROGRAM_BUILD_DATE="reproducible")

# Renode simulation build (sim/): USB is not modeled, so the XCP task drops
# its TinyUSB transport and only drains the DAQ ring. Everything else —
# scheduler, fast ISR, seqlocks, CAL pages, banked memory — is identical.
option(PICODESK_SIM "Build for Renode simulation (no USB/TinyUSB)" OFF)

set(PICODESK_SOURCES
    src/main.c
    src/spike_models.c
    src/hardfault.c
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
    FreeRTOS-Kernel
    FreeRTOS-Kernel-Heap4
)

if(PICODESK_SIM)
    target_compile_definitions(picodesk_firmware PRIVATE PICODESK_SIM=1)
else()
    target_link_libraries(picodesk_firmware tinyusb_device tinyusb_board)
endif()

# Telemetry over UART; USB CDC arrives with XCP in Phase 3.
pico_enable_stdio_uart(picodesk_firmware 1)
pico_enable_stdio_usb(picodesk_firmware 0)

# Bank-separated memory layout (BLD-002); audited by tools/check_memmap.py.
pico_set_linker_script(picodesk_firmware ${CMAKE_CURRENT_SOURCE_DIR}/ld/rp2040_banked.ld)

pico_add_extra_outputs(picodesk_firmware)
