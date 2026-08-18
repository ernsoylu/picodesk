# Vendoring Vector XCPlite — feasibility assessment

CAL-001 requires that "Vector XCPlite is integrated with a custom transport
shim for USB CDC-ACM". The tree currently ships an interim protocol core
(`target/xcp/xcp_core.c`) instead. This document records what substituting
the real library actually involves, assessed against upstream
`vectorgrp/XCPlite` as of 2026-08, so the work can be scoped rather than
discovered.

**Verdict: a real port, not a drop-in.** It is achievable, but it is a
project in its own right, and one architectural question below should be
answered before starting.

## What upstream is today

| Property | Finding | Source |
|---|---|---|
| Licence | **MIT**, © 2026 Vector Informatik | `LICENSE` |
| Supported platforms | Linux, macOS, QNX, **FreeRTOS**, Windows | `docs/BUILDING.md` |
| Library size | **~160 KB** release build, debug prints off | `docs/TECHNICAL.md` |
| Language | C11 (C++17 for the optional C++ API) | `docs/BUILDING.md` |
| Core protocol | `src/xcplite.c`, ~3,900 lines | repository |
| Transport | Ethernet-centric: `xcpethserver.c`, `xcpethtl.c`, UDP/TCP, MTU-derived segment size | `src/` |
| Default sizes | `XCPTL_MAX_CTO_SIZE` 248, `XCPTL_MAX_DTO_SIZE` 1024 | `src/xcptl_cfg.h` |

Two corrections to the SRS fall out of this: the licence is MIT, not
Apache-2.0 (§8), and the library's centre of gravity has moved to XCP **on
Ethernet** — its own description now says so.

## What the port requires

1. **A transport layer.** Upstream ships Ethernet (UDP/TCP) and shared
   memory. Neither applies. The existing `target/xcp/xcp_cdc_task.c` shim
   already speaks the framing pyxcp expects over CDC, so this is adaptation
   rather than invention — but it must be re-expressed against XCPlite's
   `xcptl.h` contract instead of the interim core's callbacks.

2. **A platform layer.** `src/platform.c` assumes sockets, threads, mutexes
   and a wall clock. On bare metal this becomes FreeRTOS primitives plus the
   RP2040 64-bit µs timer. Upstream listing FreeRTOS as supported suggests
   the seam exists; it needs verifying rather than assuming.

3. **Size reconciliation.** CTO 248 / DTO 1024 against a 64-byte CDC packet
   means either segmentation in the shim or a reconfigured build. The DAQ
   queue must also be sized against the SRAM budget, not an Ethernet MTU.

4. **Budget re-check.** ~160 KB of flash is affordable (current generated
   firmware is well under 100 KB against a 1.5 MB ceiling), but the RAM cost
   is the open number: the SRAM budget is 200 KB total with SRAM2 already at
   ~21 KB. `tools/sizing_report.py` should be re-calibrated afterwards.

5. **ARMv6-M compatibility.** Cortex-M0+ has no 32-bit atomics beyond
   load/store and no unaligned access. XCPlite's lock-less calibration
   (`docs/CAL_RCU.md`) and queue implementations should be read with that in
   mind before committing.

## The architectural question worth answering first

XCPlite's lock-less, wait-free calibration is a *different mechanism* from
the CAL page swap this system implements. RTE-003 requires that a
multi-parameter change becomes visible to the fast loop atomically **at a
`model_step()` boundary** — a property the current design guarantees because
core 0 flips the active page pointer itself, at a point it chooses.

Adopting XCPlite's RCU-style scheme instead would satisfy "calibration is
memory-safe" but not necessarily "the change lands on a step boundary". So
the integration has to decide: keep the RTE's page swap and drive it from
XCPlite's `SET_CAL_PAGE` handling (preserving RTE-003 exactly, which is what
the interim core does today), or adopt XCPlite's mechanism and revisit
RTE-003 with the customer.

**Recommendation:** keep the RTE page swap and bind XCPlite's calibration
commands to it. That preserves the requirement as written and keeps the
step-boundary guarantee under this system's control.

## Suggested sequence

1. Vendor upstream at a pinned tag into `target/xcplite/` with its `LICENSE`
   and a `NOTICE` describing PicoDesk's modifications.
2. Build `xcplite.c` alone for Cortex-M0+ with a stub platform layer; fix
   what fails to compile. This is the go/no-go step — if the protocol core
   cannot be isolated from the socket layer cheaply, stop and reconsider.
3. Re-express `xcp_cdc_task.c` against `xcptl.h`; keep the DAQ ring drain
   (RTE-005) exactly as it is.
4. Bind `SET_CAL_PAGE` to `rte_calpage_request_switch()` so RTE-003 holds.
5. Re-run: native XCP tests, `sim/picodesk_ert.robot`, the sizing gate, the
   memory audit, and the reproducibility gate.
6. Close CAL-001 only after the hardware DAQ-throughput soak (O-4), which
   emulation cannot substitute for.

## Why this was not done now

Steps 2–4 are hours of uncertain work whose outcome cannot be validated
without hardware anyway — the throughput requirement that motivates using
the real library is gated on a physical board (O-4). Shipping a
half-integrated library would be worse than the current state, which is
honest: a working interim core, clearly labelled as such in
`target/xcp/xcp_core.h`, behind the seams the real library will need.
