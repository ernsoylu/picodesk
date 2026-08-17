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

# Reproducible builds (BLD-008): identical inputs must yield an identical .uf2.
# Path prefixes are normalized for every object (SDK included) so debug info and
# __FILE__ never leak absolute paths; PICO_PROGRAM_BUILD_DATE preempts the SDK's
# __DATE__ use in binary_info.c.
add_compile_options(
    -ffile-prefix-map=${CMAKE_CURRENT_SOURCE_DIR}=SRC
    -ffile-prefix-map=${PICO_SDK_PATH}=PICO_SDK
    -ffile-prefix-map=${CMAKE_BINARY_DIR}=BUILD
)
add_compile_definitions(PICO_PROGRAM_BUILD_DATE="reproducible")

# Phase 0 walking skeleton: blink. Phases 1-3 grow this into the RTE spike
# (FreeRTOS SMP, banked linker script, RTE primitives, XCP slave).
add_executable(picodesk_firmware
    src/main.c
)

target_compile_options(picodesk_firmware PRIVATE -Wall -Werror=date-time)

target_link_libraries(picodesk_firmware
    pico_stdlib
)

pico_add_extra_outputs(picodesk_firmware)
