# Native (host) build of the RTE concurrency primitives (Phase 2).
#
# NOT SIL (out of scope, SRS 1.3) — this compiles the portable primitive
# sources exactly as the firmware uses them and stresses them under pthreads.

cmake_minimum_required(VERSION 3.20)
project(picodesk_native_tests C)

set(CMAKE_C_STANDARD 11)
add_compile_options(-Wall -Wextra -Werror -O2 -pthread)
add_compile_definitions(PICODESK_NATIVE)
link_libraries(pthread)

set(RTE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../target/rte)
include_directories(${RTE_DIR} ${CMAKE_CURRENT_SOURCE_DIR})

enable_testing()

foreach(t seqlock daq_ring calpage)
    add_executable(test_${t} test_${t}.c ${RTE_DIR}/rte_${t}.c)
    add_test(NAME ${t} COMMAND test_${t})
endforeach()
