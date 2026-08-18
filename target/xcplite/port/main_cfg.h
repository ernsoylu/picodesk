#pragma once

/* main_cfg.h — PicoDesk build configuration for vendored XCPlite V6.4.
 *
 * Derived from upstream's example main_cfg.h; the Ethernet, file-I/O and
 * A2L-generation options are removed rather than turned off, because on this
 * target they have no meaning and leaving them present invites someone to
 * switch one on and get a link error a week later.
 *
 * Two settings here are load-bearing:
 *
 *  - Debug prints are OFF. Upstream's event-registration path computes
 *    `pow(10, timeUnit)` under `#ifdef DBG_LEVEL`, and pow() on a Cortex-M0+
 *    pulls in soft-float libm — for a printf the target has nowhere to send.
 *    Turning prints off removes the only floating-point call in the library.
 *
 *  - CLOCK_USE_APP_TIME_US selects microsecond ticks, matching the RP2040
 *    64-bit timer the RTE telemetry already reads (BLD-003). xcp_cfg.h keys
 *    its DAQ timestamp unit off CLOCK_TICKS_PER_S, so this choice and
 *    picodesk_platform.h must agree.
 */

#define ON 1
#define OFF 0

/* Debug prints: see the header comment. Do not switch on for firmware. */
#define OPTION_ENABLE_DBG_PRINTS OFF

/* A2L generation is a host-side concern here: tools/make_a2l.py builds the
 * A2L from DWARF (CAL-002), so the target never writes one. */
#define OPTION_ENABLE_A2L_GEN OFF

/* Clock resolution, consumed by xcp_cfg.h and picodesk_platform.h. */
#define CLOCK_USE_APP_TIME_US
