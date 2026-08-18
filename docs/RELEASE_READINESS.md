# Release readiness — v1.0

**Status: not releasable as v1.0.** Everything the SRS specifies is
implemented, and every requirement is verified as far as host testing and
emulation can reach — but seven gates need a physical RP2040, and one needs
a licensing decision. Tagging v1.0 now would claim validation that has not
happened.

This document says exactly what is done, what is not, and what closing the
gap requires.

## What is verified, automatically, on every push

| Job | Covers |
|---|---|
| host / lint | ruff over `host`, `tests`, `tools` |
| host / pytest + GUI | 85 tests: extraction, gating, MAT-002, routing rules, generator, sizing, A2L/DWARF, GUI view-models and widgets (offscreen Qt) |
| host / Windows | the same host suite plus generation and the sizing gate on `windows-latest` (SRS §1.3 platform scope) |
| native / RTE + XCP | pthread stress on seqlock, DAQ ring, CAL pages; XCP protocol conformance |
| sim / Renode | spike firmware, generated firmware, fault-injection drills and the 28-model scale workspace, all on an emulated dual-core RP2040 |
| firmware / reproducibility | pinned ARM GCC 12.2.rel1, memory-bank audit, bit-identical UF2 across independent build trees |

`tools/traceability.py` regenerates [TRACEABILITY.md](TRACEABILITY.md) and
**fails the build** if any cited test has been renamed or deleted, or if the
SRS gains a requirement the matrix does not cover. Coverage claims cannot
quietly rot.

## Open gates

### Hardware (7)

None of these can be closed in emulation. Renode models function, not time,
and does not implement the RP2040 USB block.

| Requirement | Gate | What it needs |
|---|---|---|
| NFR-1 | jitter ≤ 50 µs p99.99 under full DAQ load, 10⁶ cycles | Pico + logic analyzer on GPIO 2 (ISR active) and GPIO 3 (overrun) |
| NFR-3 | no critical section over 15 µs on either core | hold-time probe campaign |
| RTE-004 | 24 h two-core soak, torn-read detector | Pico, long run |
| CAL-001 | sustained ≥ 50 signals at 100 Hz over real USB CDC | Pico + `tests/hil/xcp_smoke.py` |
| CAL-001 | Vector XCPlite v5 in place of the interim protocol core | vendoring + re-run of the DAQ load test |
| GUI-012 | MDF4 recording from a live DAQ stream | Pico + asammdf round trip |
| BLD-006 | HardFault **exception dispatch** | Pico; Renode halts the core with "CPU abort" instead of vectoring. Everything downstream (record write, watchdog reboot, persistence across reset, boot report) is already proven by the assert drill, which shares that path. |

### Licensing (1)

PyQt6 is GPL-3.0 or commercial. Distributing a PicoDesk binary built against
the GPL build makes the whole application GPL. This is a business decision
and gates any proprietary release. See
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

### Substitutions still owed

- **Vector XCPlite** replaces `target/xcp/xcp_core.c`. The interim core
  speaks the same wire protocol behind the same seams (transport callback,
  CAL page hooks, event-driven DAQ), so the swap should not disturb the
  transport or the RTE — but it must be done and re-tested, and the
  Apache-2.0 licence and NOTICE retained.
- **ERT-generated step code** replaces the hand-written stand-ins in the
  generated firmware. Real Embedded Coder output is already proven to build
  integer-only step code from a fixed-point fixture
  (`tests/matlab_live/test_live_matlab.py`); wiring it into the generated
  image is the remaining step.
- **MATLAB version matrix.** The SRS pins R2023b–R2024b; the machine used
  for live validation runs R2025b. The alignment checker flags this by
  design. Either widen the SRS matrix after testing a pinned release, or
  validate on one.

### Deferred by scope (SRS §1.3, not gaps)

NvM persistence, on-target GDB/OpenOCD debugging, host-side SIL, XCP
seed/key security, Apple Silicon.

## Recommended path to v1.0

1. Vendor XCPlite; re-run the native protocol suite and the Renode suites.
2. Wire ERT-emitted step code into the generated firmware; re-run the
   generated-RTE and scale system tests.
3. Bring up hardware: flash the spike firmware, run the fault drills
   (including the HardFault injection Renode cannot dispatch), then the
   watchdog drills.
4. Run the NFR-1 campaign unloaded, then under 50 × 100 Hz DAQ; archive the
   histograms. Run the NFR-3 probe campaign.
5. Run the 24 h RTE-004 soak and the CAL-001 throughput soak.
6. Decide Qt licensing; assemble the licence bundle.
7. Re-run `tools/traceability.py`; when no hardware gates remain, tag v1.0.

Until step 7 reports zero gates, the honest version number is a pre-release.
