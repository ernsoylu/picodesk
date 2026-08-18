# Problem inventory

Every problem encountered building PicoDesk, sorted by whether it still
blocks you and, within that, by what it cost or would have cost. Closed
entries are kept deliberately: several describe traps that will recur, and
the pattern at the end is the most useful thing in this file.

Counts: **28 total — 11 open, 17 closed.**

---

## Open — blocks tagging v1.0 (8)

Seven need a physical RP2040. One needs a decision.

| ID | Problem | Requirement | Why it cannot close in CI |
|---|---|---|---|
| O-1 | Jitter ≤ 50 µs p99.99 under full DAQ load, 10⁶ cycles | NFR-1 | Virtual time is not real time; needs a logic analyzer on GPIO 2/3 |
| O-2 | No critical section over 15 µs on either core | NFR-3 | Needs hold-time probes on hardware |
| O-3 | 24 h two-core soak with a torn-read detector | RTE-004 | Needs a board running for a day |
| O-4 | Sustained ≥ 50 signals at 100 Hz over USB CDC | CAL-001 | The RP2040 USB block is unmodeled in Renode |
| O-5 | MDF4 recording from a live DAQ stream | GUI-012 | Depends on O-4 |
| O-6 | HardFault **exception dispatch** | BLD-006 | Renode halts the core with "CPU abort" instead of vectoring |
| O-7 | Vendor Vector XCPlite v5 in place of the interim core | CAL-001 | Substitution + re-test; also an Apache-2.0 obligation |
| O-8 | **Qt/PyQt licensing: GPL-3.0 or commercial** | — | Business decision; gates any proprietary distribution |

O-6 is the only true emulator limitation rather than a timing gate.
Everything downstream of the vector — record write, watchdog reboot,
persistence across reset, boot report — is already proven by the assert
drill, which shares that entire path.

## Open — known limitations, not blockers (3)

| ID | Problem | Where |
|---|---|---|
| O-10 | Array signals (`width > 1`) rejected by design | `host/picodesk/rtegen/routing.py:176` |
| O-11 | Validated on MATLAB R2025b; SRS pins R2023b–R2024b | `host/picodesk/matlab_bridge/session.py` |
| O-12 | Interim XCP protocol core marked as Phase 3 debt | `target/xcp/xcp_core.h` |

O-9 is **closed** — see C-7. O-10 still waits on array-signal support in
the adapter. O-11 is a spec decision — widen the matrix after testing a pinned release, or validate on
one. The alignment checker flags the mismatch by design rather than
silently accepting it.

---

## Closed — product defects (7)

Ranked by what shipping them would have cost.

**C-1 · Generated model steps executed from XIP flash, not SRAM** (BLD-001,
fixed in `phase8`). Systematic, silent, present in every workspace. The
dispatcher was SRAM-resident but called into flash — precisely the jitter
source the banked-memory architecture exists to eliminate. Nothing failed;
emulation hides it because virtual time is not real time. The natural
discovery point was the final NFR-1 campaign, after every other phase had
been built on the assumption that BLD-001 held. Fixed by emitting a
`PD_<NAME>_STEP` macro that applies `__not_in_flash_func` to fast-loop step
*definitions*.

**C-2 · Seqlock clobbered last-known-good on retry exhaustion** (RTE-004,
fixed in `phase2`). On failure it wrote a torn copy into the caller's
shadow — exactly inverting the required fallback. Manifests as intermittent
bad values in a control loop with no diagnostic trace. Caught immediately by
the pthread stress test that was written to look for it.

**C-3 · Console hyperlinks were inert** (GUI-005, fixed in `phase7`).
`QPlainTextEdit` renders anchors but cannot emit a click, so "hyperlinked to
the source line" was blue text that did nothing. Now a `QTextBrowser` with
click routing.

**C-4 · `QUrl` lowercased the file path** (GUI-005, fixed in `phase7`). The
link scheme carried the path in the URL *host*, which QUrl normalises to
lowercase per RFC. Jump-to-source would have opened the wrong file on any
case-sensitive filesystem. The path now travels in the query string.

**C-5 · Sizing estimate wrong per bank** (BLD-004, fixed in `phase6`). The
total was within tolerance, but splitting runtime overhead 50/50 put the
16 kB kernel heap in the wrong bank — so a bank overflow could have passed
the check that exists to catch it. Recalibrated against real linker maps to
−0.9 % total, ≤ 5 % per bank.

**C-7 · ERT fast-loop model code executed from flash** (BLD-001, fixed
while closing O-9). The same class as C-1, one level deeper and found the
same way. The generated adapter reached SRAM via `__not_in_flash_func`, but
the ERT code it calls stayed at `0x1000061c` in flash: the linker rule
placing `models/fast/*` in RAM never matched, because the FLASH `.text`
wildcard consumes everything first and first match wins. Fixed by excluding
`*/models/fast/*` from the flash sections. The audit could not see it
either, so it now **derives** the requirement: an SRAM-resident adapter
`pd_<Model>_step` implies that model is fast-loop, so the `<Model>_step` it
calls must be SRAM-resident too — no model list to maintain, nothing to go
stale.

**C-6 · Memory audit was blind to generated firmware** (`tools/check_memmap.py`,
fixed in `phase8`). It hard-coded the spike firmware's symbol names, so it
could not see generated firmware at all. That is how C-1 hid. Now
section-driven: it verifies whatever is *declared* time-critical, and
ignores functions the linker garbage-collected.

## Closed — validation defects (4)

Green tests that proved nothing. Dangerous in proportion to the confidence
they carry, and these carried safety confidence.

**V-1 · Unaligned-load fault injection never faulted** — GCC split the
32-bit load from an odd address into four byte loads, and low addresses map
to bootrom anyway.

**V-2 · Undefined-instruction injection never trapped** — not dispatched by
the emulator. Both V-1 and V-2 would have produced passing BLD-006/BLD-007
drills that exercised nothing. Replaced with a fetch from unmapped memory,
which genuinely aborts, plus an honest split: the assert drill covers
everything downstream of the vector, and dispatch is documented as O-6.

**V-4 · Self-exciting stand-ins were flattering every generated-firmware
test.** The hand-written model stand-ins generated their own motion, so
"signals are moving" partly measured the stimulus rather than proving the
mesh carried data. Real ERT models are purely reactive: with an
unstimulated ADC reading zero, the whole chain rested at a legitimate
zero fixed point and the cross-core assertion failed. The fixture is now a
closed loop with a setpoint, so a nonzero derate genuinely proves the slow
model consumed fast-loop output and its result returned through the
opposite seqlock bus.

**V-3 · GUI test asserted on the wrong row** — expected a type-mismatch
rejection on a port that was actually already bound. Masked because
"already bound" is correctly reported *ahead* of a type mismatch.

## Closed — third-party defects and limitations (5)

**T-1 · Renode SIO spinlock slot-0 collision — ACTION OUTSTANDING.** Lock
ownership is stored as the CPU slot index, so slot 0 is indistinguishable
from the "free" sentinel; both cores could hold a FreeRTOS SMP kernel lock
simultaneously, asserting in `vPortRecursiveLock`. It also caused a ~1000×
emulation slowdown that looked like a hang. Patched locally in
`sim/patches/rp2040_sio.cs`. **Should be reported upstream to
`matgla/Renode_RP2040` so the patch can be dropped.**

**T-2 · Renode maps no non-striped SRAM bank windows** — the banked linker
script needs 0x2100_0000–0x2103_FFFF. Worked around with a board overlay
(`sim/picodesk_board.repl`).

**T-3 · MATLAB `jsonencode` collapses single-element struct arrays** — a
one-port model arrived as an object rather than a list. Normalised on the
Python side.

**T-4 · MATLAB `onCleanup` destruction order is unspecified** — two locals
meant `close_system` ran on a still-compiled model. Replaced with a single
term-then-close cleanup.

**T-5 · `GenCodeOnly`, not `GenerateCodeOnly`** — the Embedded Coder
parameter name, verified against the installed release.

## Closed — environment friction (6)

Cost time, would never have shipped: setuptools rejecting a readme outside
the package root (broke the editable install in CI); Robot Framework parsing
any `identifier=` argument as *named* (cost three separate debugging rounds
— `cal_sw=…`, `dhb=0`, and bool coercion), plus `treatAsRegex` naming and
lowercase-`true` coercion; an orphaned test-runner process holding the
Robot port for 2½ hours while silently queuing every rerun; `matlabengine`
refusing to install from the root-owned MATLAB tree (used the PyPI package);
`logLevel 3` suppressing the very UART output being debugged; and a
generated C comment that documented the linker glob `*/models/fast/*`
inline, whose `*/` terminated the comment and turned the rest of the
sentence into syntax errors.

---

## The pattern

**Every product defect that would have shipped was found by running
something** — pthread stress, emulated hardware, a 28-model workspace, or
looking at a rendered screenshot. None were found by reading code.

Three corollaries worth keeping:

1. **Scale surfaces what fixtures hide.** C-1 was invisible in the
   two-model fixture and obvious at 28 models.
2. **A test that cannot fail is worse than no test.** V-1 and V-2 were
   green and meaningless. Before trusting a drill, verify the mechanism
   actually fires.
3. **Tools that check code must themselves be checked.** C-6 was a blind
   spot in the auditor, and it concealed C-1; the same blind spot then
   concealed C-7. Both times the fix was to make the check *derive* its
   requirement from the artefact rather than from a hard-coded list. The
   traceability harness is built on the same principle: it verifies its own
   evidence, and was validated by deliberately breaking four entries.
4. **Convenient fixtures flatter the system under test.** V-4: stand-ins
   that move on their own make a data path look alive whether or not it
   carries anything. Substituting the real thing is what exposed it.
