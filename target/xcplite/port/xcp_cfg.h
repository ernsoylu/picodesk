#pragma once

/* xcp_cfg.h — protocol layer configuration for PicoDesk.
 *
 * Deltas from upstream's example configuration, and why:
 *
 *  - XCP_ENABLE_CAL_PAGE is ON. This is the whole point of the integration:
 *    it routes SET_CAL_PAGE/GET_CAL_PAGE to ApplXcpSetCalPage/GetCalPage in
 *    picodesk_xcp_appl.c, which arm the RTE's own page swap so the change
 *    lands on a model_step() boundary (RTE-003).
 *
 *  - XCP_ENABLE_IDT_A2L_UPLOAD is OFF. The target holds no A2L; the host
 *    builds it from DWARF (CAL-002), so upload would only advertise a
 *    capability that returns nothing.
 *
 *  - XCP_ENABLE_MULTITHREAD_EVENTS stays OFF. XcpEvent is called from the
 *    core 0 fast ISR and nowhere else, so there is no concurrent caller to
 *    guard against — and taking a mutex there would put the XCP layer inside
 *    the 15 us critical-section budget (NFR-3), which is exactly what the
 *    DAQ ring (RTE-005) exists to avoid.
 *
 *  - XCP_ENABLE_TEST_CHECKS stays ON. Upstream flags it as a performance
 *    penalty, but it only guards command handling on core 1; the fast path
 *    does not execute it.
 */

/* Version */
#define XCP_DRIVER_VERSION 0x01
#define XCP_PROTOCOL_LAYER_VERSION 0x0104

/* Protocol features */
#define XCP_ENABLE_CAL_PAGE
#define XCP_ENABLE_CHECKSUM

/* DAQ features and parameters */
#define XCP_ENABLE_DAQ_EVENT_LIST
#define XCP_MAX_EVENT 16

/* 5 bytes per ODT entry. CAL-001 asks for a DAQ list of >= 50 signals;
 * 2 KB leaves room for that plus the DAQ list and ODT descriptors, and is
 * accounted for in tools/sizing_report.py against the SRAM2 budget. */
#define XCP_DAQ_MEM_SIZE 2048

#define XCP_DAQ_CLOCK_32BIT

#if CLOCK_TICKS_PER_S == 1000000
#define XCP_TIMESTAMP_UNIT DAQ_TIMESTAMP_UNIT_1US
#define XCP_TIMESTAMP_TICKS 1
#else
#error "PicoDesk expects the 1 us clock selected by CLOCK_USE_APP_TIME_US"
#endif

/* Extended error checks (core 1 command path only). */
#define XCP_ENABLE_TEST_CHECKS
