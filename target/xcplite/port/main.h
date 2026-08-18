/* PicoDesk umbrella header for vendored XCPlite V6.4.
 *
 * XCPlite expects the integrating project to own main.h and the *_cfg.h
 * set; upstream's own main.h is a Windows/Linux split that pulls sockets,
 * pthreads and ifaddrs.h, none of which exist on a bare-metal RP2040.
 * Replacing it here is the documented integration seam, and it means the
 * vendored sources in ../vendor stay byte-identical to upstream.
 */
#ifndef PICODESK_XCPLITE_MAIN_H
#define PICODESK_XCPLITE_MAIN_H

#include <assert.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_PATH 256
#ifndef BOOL
#define BOOL int
#define FALSE 0
#define TRUE 1
#endif

#include "main_cfg.h"
#include "picodesk_platform.h"
#include "dbg_print.h"

#endif /* PICODESK_XCPLITE_MAIN_H */
