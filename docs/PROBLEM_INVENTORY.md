# Problem inventory

Every problem encountered building PicoDesk, sorted by whether it still
blocks you and, within that, by what it cost or would have cost. Closed
entries are kept deliberately: several describe traps that will recur, and
the pattern at the end is the most useful thing in this file.

Counts: **43 total — 9 open, 34 closed.**

---

## Open — blocks tagging v1.0 (6)

All six need a physical RP2040.

| ID | Problem | Requirement | Why it cannot close in CI |
|---|---|---|---|
| O-1 | Jitter ≤ 50 µs p99.99 under full DAQ load, 10⁶ cycles | NFR-1 | Virtual time is not real time; needs a logic analyzer on GPIO 2/3 |
| O-2 | No critical section over 15 µs on either core | NFR-3 | Needs hold-time probes on hardware |
| O-3 | 24 h two-core soak with a torn-read detector | RTE-004 | Needs a board running for a day |
| O-4 | Sustained ≥ 50 signals at 100 Hz over USB CDC | CAL-001 | The RP2040 USB block is unmodeled in Renode |
| O-5 | MDF4 recording from a live DAQ stream | GUI-012 | Depends on O-4 |
| O-6 | HardFault **exception dispatch** | BLD-006 | Renode halts the core with "CPU abort" instead of vectoring |

All six now have a written procedure, wiring diagram and analysis script in
[HARDWARE_CAMPAIGN.md](HARDWARE_CAMPAIGN.md), with the analysis unit-tested
against synthetic captures (`tests/test_hil_analyze.py`). What is left is
bench time, not method.

O-6 is the only true emulator limitation rather than a timing gate.
Everything downstream of the vector — record write, watchdog reboot,
persistence across reset, boot report — is already proven by the assert
drill, which shares that entire path.

## Open — real-workspace gaps (2 of 9 remain)

The first externally authored workspace (`examples/models`, a typical
interface-first Simulink development cycle with shared data dictionaries)
did not survive first contact with the pipeline. Seven of the nine gaps are
now **fixed and live-verified** — the workspace extracts cleanly, shared-
dictionary edits invalidate the cache, rate groups are assigned on the
models page and forced at codegen, generated identifiers are model-prefixed,
and model3's interface contradiction is diagnosed at the source. The
evidence-backed register with fix locations is
[EXAMPLES_GAP_ANALYSIS.md](EXAMPLES_GAP_ANALYSIS.md). What remains needs a
decision, not a patch:

| ID | Gap | Trace |
|---|---|---|
| G-6 | **Narrowed to UART input only:** GPIO endpoints are boolean and the conditioning-model idiom covers physical-units ADC (see the user guide + `examples/routing.json`); a UART input channel still needs a design — UART0 is owned by stdio/telemetry | GUI-006 |
| G-8 | Dictionary `Simulink.Parameter` calibratables are inlined by ERT — recorded and surfaced as a diagnostic, but a tunable-parameter policy (CAL-segment placement, A2L pickup) does not exist yet — **in progress by decision: full policy** | GUI-012/RTE-003 |

Closed: G-1/G-9 (dictionary path + per-model errors, engine no longer
restarted for MATLAB-side errors), G-2 (descriptor v2 `rate_group: null`,
routing v2 `rate_assignments`, MAT-002 at assignment, codegen rate forcing),
G-3 (identifier rules forced at codegen), G-4 (dictionary closure hashed
into the cache gate), G-5 (HAL writer identity is `(function, hal_arg)`),
G-7 (extraction checks ports against the dictionary catalogue).

## Open — known limitations, not blockers (1)

| ID | Problem | Where |
|---|---|---|
| O-12 | Two XCP slaves in the tree; the interim core is still the default | `target/xcp/`, `target/xcplite/` |

O-12 is what remains of O-7 after the port. Vendored XCPlite is integrated,
tested and CI-gated behind `-DPICODESK_XCPLITE=ON`; flipping it to the default
and deleting `target/xcp/` waits on the O-4 throughput soak, because the
throughput number is the only reason to prefer the library and it is the one
thing emulation cannot measure.

O-7, O-9 and O-10 are **closed** (see C-8, C-7, and the array-signal work).
Two more are **closed by decision** rather than by work:

- **O-11** — R2025b is the release in use, so the SRS was amended to pin it
  (v7.1) rather than validate against a toolchain nobody runs.
- **O-8** — Qt stays, under GPL-3.0; the commercial option was declined. The
  consequence is that PicoDesk itself is GPL-3.0-or-later (root `LICENSE`),
  and distributing a build carries a source offer. Internal use carries
  nothing, because it is not distribution. The firmware image is untouched by
  this: it links no Qt. See
  [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

---

## Closed — product defects (9)

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

**C-6 · Memory audit was blind to generated firmware** (`tools/check_memmap.py`,
fixed in `phase8`). It hard-coded the spike firmware's symbol names, so it
could not see generated firmware at all. That is how C-1 hid. Now
section-driven: it verifies whatever is *declared* time-critical, and
ignores functions the linker garbage-collected.

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

**C-9 · The Live Tuning panel was a shell over two stubs** (GUI-012, fixed
after the XCPlite port). `XcpMaster` and `Mdf4Logger` were sixteen and
thirteen lines of `raise NotImplementedError`, and the calibration page was
instantiated into the stack and then never referenced again — its
`connectRequested` and `recordToggled` signals were connected to nothing. So
Connect and Record MDF4 did nothing at all, in a build where every other page
was wired to its real backend. Found by grepping for stubs while surveying
what could be done without hardware, not by any test: the widget tests cover
rendering, and rendering was fine. Now implemented, with the wire operations
behind a narrow `XcpBackend` seam so the whole chain is driven in-process
against a slave that implements the target's real CAL-page semantics.

**C-8 · CAL-001 named a library the tree did not contain** (fixed while
closing O-7). The interim protocol core was honest about being interim, but a
requirement that names Vector XCPlite is not satisfied by something that
merely speaks the same wire protocol. The library is now vendored unmodified
at tag V6.4 with a PicoDesk port supplying the transport, platform and
application layers. The substitution is proved rather than assumed:
`tests/native/test_xcplite.c` runs the interim core's own master-side sequence
against the real library through the real CDC framing, and CI boots the
substituted firmware through the full Renode suite. Cost: +1.6 kB flash,
+4.9 kB RAM.

## Closed — validation defects (7)

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

**V-7 · Five Renode suites parsed machine lines by position.** Every
generated-firmware suite indexed telemetry regex groups 1..9, and the
generated line is not even fixed in shape — its fields depend on how many rate
groups and debug signals the workspace has. The faults suite did the same to
the eight-field FAULT record. Inserting a field shifts every later index by
one: the suite keeps passing, but each assertion is now checking a different
number than it names. Found while appending `crit_max` to the telemetry line,
which would have done exactly that. All five now match by name through one
shared keyword (`sim/telemetry.resource`, hex-aware for the FAULT record's
`0x%08lx` fields), which also makes them tolerant of a firmware built before
or after a field was added.

**V-6 · GUI-012's hardware gate was concealing an unbuilt feature.** The
traceability matrix carried the requirement on two widget-rendering tests, a
pointer at the HIL script, and a hardware note for "MDF4 recording from a live
DAQ stream". Every artefact existed, so the self-verifying matrix was happy —
but nothing cited covered pyxcp parameter adjustment or MDF4 writing, and the
code that would have was C-9's stub. A `status: hardware` gate is a claim that
the work is done and only the measurement is missing; it is worth checking
that is true before writing one.

**V-3 · GUI test asserted on the wrong row** — expected a type-mismatch
rejection on a port that was actually already bound. Masked because
"already bound" is correctly reported *ahead* of a type mismatch.

**V-4 · Self-exciting stand-ins were flattering every generated-firmware
test.** The hand-written model stand-ins generated their own motion, so
"signals are moving" partly measured the stimulus rather than proving the
mesh carried data. Real ERT models are purely reactive: with an
unstimulated ADC reading zero, the whole chain rested at a legitimate
zero fixed point and the cross-core assertion failed. The fixture is now a
closed loop with a setpoint, so a nonzero derate genuinely proves the slow
model consumed fast-loop output and its result returned through the
opposite seqlock bus.

**V-5 · The NFR-3 telemetry field was never written.** `crit_max_us` sat in
the telemetry struct labelled "NFR-3 probe" with nothing assigning it — a
measurement that existed only as a comment. Instrumenting it then produced the
V-1/V-2 shape immediately: the seqlock write finishes inside one 1 us tick, so
the probe reported 0, which is indistinguishable from a probe that is not in
the code path. Fixed by reporting a sample count alongside the max, so
`crit_max=0 crit_n=6195` reads as "measured, too fast to resolve" and
`crit_n=0` reads as "broken build" — and by a native test that asserts the
writer actually invokes the probe, which is the part that can silently stop
being true.

## Closed — third-party defects and limitations (7)

**T-1 · Renode SIO spinlock slot-0 collision — reported upstream.** Lock
ownership is stored as the CPU slot index, so slot 0 is indistinguishable
from the "free" sentinel; both cores could hold a FreeRTOS SMP kernel lock
simultaneously, asserting in `vPortRecursiveLock`. It also caused a ~1000×
emulation slowdown that looked like a hang. Patched locally in
`sim/patches/rp2040_sio.cs`; reported to `matgla/Renode_RP2040` as
[issue #25](https://github.com/matgla/Renode_RP2040/issues/25) with the fix,
so the local patch can be dropped once it lands upstream.

**T-2 · Renode maps no non-striped SRAM bank windows** — the banked linker
script needs 0x2100_0000–0x2103_FFFF. Worked around with a board overlay
(`sim/picodesk_board.repl`).

**T-3 · MATLAB `jsonencode` collapses single-element struct arrays** — a
one-port model arrived as an object rather than a list. Normalised on the
Python side.

**T-4 · MATLAB `onCleanup` destruction order is unspecified** — two locals
meant `close_system` ran on a still-compiled model. Replaced with a single
term-then-close cleanup.

**T-6 · SRS §8 states XCPlite is Apache-2.0; upstream is MIT.** Corrected in
the licence inventory; the SRS itself should be amended.

**T-5 · `GenCodeOnly`, not `GenerateCodeOnly`** — the Embedded Coder
parameter name, verified against the installed release.

**T-7 · XCPlite writes the DAQ timestamp unaligned — HardFault on
Cortex-M0+.** `xcpLite.c` does `*((uint32_t*)&d0[2]) = clock`, a 32-bit store
at offset 2 of the buffer the transport layer hands it. ARMv6-M has no
unaligned access, so this is a fault, not a slow path; it is invisible on every
architecture upstream tests on. It also cost the only real debugging round of
the port — the symptom was a segfault in `XcpEvent_` on the *host*, from a
different cause (32-bit address truncation in the test's DAQ rebase), which
masked it. Absorbed in the port by returning a buffer congruent to 2 mod 4
(`TX_STAGING_SKEW`) rather than patching the vendored file, so re-vendoring
stays a copy. A newer upstream must be re-checked at that offset.

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

Five corollaries worth keeping:

1. **Scale surfaces what fixtures hide.** C-1 was invisible in the
   two-model fixture and obvious at 28 models.
2. **A test that cannot fail is worse than no test.** V-1 and V-2 were
   green and meaningless. Before trusting a drill, verify the mechanism
   actually fires. V-5 adds the sharper form: a *measurement* whose healthy
   reading is zero must also report whether it ran, or "fine" and "not
   wired up" are the same number on the screen.
3. **A hardware gate is not a parking space.** V-6: "needs a board" reads
   as *built, pending measurement*. Used on something unbuilt it hides the gap
   behind a legitimate-looking blocker, and the self-verifying matrix cannot
   catch it, because every artefact cited really does exist.
4. **Tools that check code must themselves be checked.** C-6 was a blind
   spot in the auditor, and it concealed C-1; the same blind spot then
   concealed C-7. Both times the fix was to make the check *derive* its
   requirement from the artefact rather than from a hard-coded list. The
   traceability harness is built on the same principle: it verifies its own
   evidence, and was validated by deliberately breaking four entries.
5. **Convenient fixtures flatter the system under test.** V-4: stand-ins
   that move on their own make a data path look alive whether or not it
   carries anything. Substituting the real thing is what exposed it.
