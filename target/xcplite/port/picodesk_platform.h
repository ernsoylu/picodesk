/* Platform shim for vendored XCPlite on RP2040 / FreeRTOS SMP.
 *
 * Upstream platform.h is socket- and pthread-oriented. The protocol core
 * only needs three mutex operations and a clock, so this shim provides
 * exactly those and nothing else — no sockets, no threads, no files.
 *
 * The mutex maps to a FreeRTOS recursive mutex. It is taken only by the
 * XCP task on core 1, never by the core 0 fast path, so it cannot affect
 * the 15 us critical-section budget (NFR-3).
 *
 * The clock is the RP2040 64-bit microsecond timer, the same source the
 * RTE telemetry uses (BLD-003).
 */
#ifndef PICODESK_XCPLITE_PLATFORM_H
#define PICODESK_XCPLITE_PLATFORM_H

#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>

/* Self-contained: main.h includes main_cfg.h first, but picodesk_platform.c
 * includes only this header. */
#include "main_cfg.h"

/* Clock resolution. main_cfg.h selects CLOCK_USE_APP_TIME_US; xcp_cfg.h reads
 * CLOCK_TICKS_PER_S to pick the DAQ timestamp unit, and the transport layer
 * reads CLOCK_TICKS_PER_MS for its flush cycle. Upstream defines these in its
 * own platform.h, which is the file this one replaces. */
#ifndef CLOCK_USE_APP_TIME_US
#error "PicoDesk builds XCPlite with the 1 us application clock"
#endif
#define CLOCK_TICKS_PER_S 1000000
#define CLOCK_TICKS_PER_MS 1000
#define CLOCK_TICKS_PER_US 1

/* XCPlite declares APIs in terms of these names. */
typedef void *MUTEX;

void mutexInit(MUTEX *m, bool recursive, uint32_t spinCount);
void mutexDestroy(MUTEX *m);
void mutexLock(MUTEX *m);
void mutexUnlock(MUTEX *m);

/* Monotonic clock in XCPlite ticks (configured to 1 us in xcp_cfg.h). */
uint64_t clockGet(void);
bool clockInit(void);
char *clockGetString(char *s, uint32_t l, uint64_t c);

#endif /* PICODESK_XCPLITE_PLATFORM_H */
