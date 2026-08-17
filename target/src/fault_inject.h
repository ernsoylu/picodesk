/* Fault-injection console for the BLD-006 / BLD-007 drills.
 *
 * A single-character command channel on stdin, polled by the stats task.
 * Present in every build: these are the only ways to exercise the
 * post-mortem and watchdog paths, on hardware and in emulation alike
 * (on-target debugging is out of scope, SRS 1.3).
 *
 *   'h'  unaligned/illegal access -> HardFault (BLD-006)
 *   'a'  failed configASSERT      -> assert record (BLD-006)
 *   'w'  wedge the fast ISR       -> cross-core watchdog reset (BLD-007)
 *   'm'  stall the monitor task   -> watchdog reset (BLD-007)
 */

#ifndef PICODESK_FAULT_INJECT_H
#define PICODESK_FAULT_INJECT_H

#include <stdbool.h>
#include <stdint.h>

/* Poll stdin; execute any pending injection command. Non-blocking. */
void fault_inject_poll(void);

/* True once 'w' was received: the fast ISR spins instead of ticking, so the
 * heartbeat stops advancing while core 1 stays healthy. Checked by the ISR. */
bool fault_inject_isr_wedged(void);

#endif /* PICODESK_FAULT_INJECT_H */
