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
set(XCP_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../target/xcp)
include_directories(${RTE_DIR} ${XCP_DIR} ${CMAKE_CURRENT_SOURCE_DIR})

enable_testing()

foreach(t seqlock daq_ring calpage)
    add_executable(test_${t} test_${t}.c ${RTE_DIR}/rte_${t}.c)
    add_test(NAME ${t} COMMAND test_${t})
endforeach()

# NFR-3 probe. Built with the option ON precisely because the probe's own
# failure mode is being compiled out or falling out of the code path unnoticed.
add_executable(test_crit_probe test_crit_probe.c ${RTE_DIR}/rte_seqlock.c)
target_compile_definitions(test_crit_probe PRIVATE PICODESK_NFR3_PROBE=1)
add_test(NAME crit_probe COMMAND test_crit_probe)

# Phase 3: XCP protocol core (CAL-001 interim slave) against real CAL pages.
add_executable(test_xcp_core test_xcp_core.c ${XCP_DIR}/xcp_core.c
               ${RTE_DIR}/rte_calpage.c)
add_test(NAME xcp_core COMMAND test_xcp_core)

# Vendored Vector XCPlite behind the PicoDesk port (CAL-001). Same master-side
# sequence as test_xcp_core, run against the real library, so the substitution
# is proved rather than assumed. The vendored source is third-party and is not
# held to -Wall -Wextra -Werror; the port sources are.
set(XCPLITE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../target/xcplite)
add_executable(test_xcplite test_xcplite.c
               ${XCPLITE_DIR}/vendor/xcpLite.c
               ${XCPLITE_DIR}/port/picodesk_platform.c
               ${XCPLITE_DIR}/port/picodesk_xcp_tl.c
               ${XCPLITE_DIR}/port/picodesk_xcp_appl.c
               ${RTE_DIR}/rte_calpage.c)
target_include_directories(test_xcplite BEFORE PRIVATE
                           ${XCPLITE_DIR}/port ${XCPLITE_DIR}/vendor)
set_source_files_properties(${XCPLITE_DIR}/vendor/xcpLite.c PROPERTIES
                            COMPILE_OPTIONS "-w")
add_test(NAME xcplite COMMAND test_xcplite)
