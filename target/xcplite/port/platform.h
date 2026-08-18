/* Upstream XCPlite includes "platform.h" by that exact name. Ours lives in
 * picodesk_platform.h so the PicoDesk-authored file is never confused with
 * the vendored one it replaces; this header exists purely so the vendored
 * sources in ../vendor can stay byte-identical to upstream.
 */
#ifndef PICODESK_XCPLITE_PLATFORM_FORWARD_H
#define PICODESK_XCPLITE_PLATFORM_FORWARD_H

#include "picodesk_platform.h"

#endif /* PICODESK_XCPLITE_PLATFORM_FORWARD_H */
