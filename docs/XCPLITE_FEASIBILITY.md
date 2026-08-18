# Vendoring Vector XCPlite — assessment and outcome

CAL-001 requires that "Vector XCPlite is integrated with a custom transport
shim for USB CDC-ACM". This document was written first as a feasibility
assessment, to scope the work rather than discover it. The port has since been
done; the outcome is recorded at the bottom, including the two places where the
assessment was wrong.

**Status: integrated behind `-DPICODESK_XCPLITE=ON`.** The interim core remains
the default until the hardware DAQ-throughput soak (O-4) has run on the new
path — emulation cannot substitute for that measurement.

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
Apache-2.0 (§8, amended in v7.1), and the library's centre of gravity has moved
to XCP **on Ethernet** — its own description now says so.

## What the port required

1. **A transport layer.** Upstream ships Ethernet (UDP/TCP) and shared memory.
   Neither applies. `target/xcplite/port/picodesk_xcp_tl.c` implements the
   `xcpTl.h` contract over CDC.

2. **A platform layer.** `src/platform.c` assumes sockets, threads, mutexes and
   a wall clock. `port/picodesk_platform.c` provides the three mutex operations
   and the clock, over FreeRTOS and the RP2040 64-bit µs timer.

3. **Size reconciliation.** CTO 248 / DTO 1024 against a 64-byte CDC packet.
   Resolved by reconfiguring rather than segmenting: `port/xcptl_cfg.h` sets
   CTO 64 / DTO 64, so one XCP message is one USB packet.

4. **Budget re-check.** See "What it cost" below.

5. **ARMv6-M compatibility.** Cortex-M0+ has no 32-bit atomics beyond
   load/store and no unaligned access. This is where the real defect was
   found — see below.

## The architectural question, and how it was answered

XCPlite's lock-less, wait-free calibration is a *different mechanism* from the
CAL page swap this system implements. RTE-003 requires that a multi-parameter
change becomes visible to the fast loop atomically **at a `model_step()`
boundary** — a property the current design guarantees because core 0 flips the
active page pointer itself, at a point it chooses.

**Decision: keep the RTE page swap and bind XCPlite's calibration commands to
it.** `XCP_ENABLE_CAL_PAGE` routes `SET_CAL_PAGE` to
`ApplXcpSetCalPage()` in `port/picodesk_xcp_appl.c`, which only calls
`rte_cal_request_switch()`; core 0 still commits. `ApplXcpGetPointer()`
redirects every access inside the CAL window to the offline page, so a
`DOWNLOAD` stays invisible until that commit. This preserves RTE-003 as
written, and `tests/native/test_xcplite.c` asserts it directly: after
`SET_CAL_PAGE` the active page still reads the old value, and only
`rte_calpage_commit()` makes the new one visible.

## What it cost

Whole-firmware delta from `-DPICODESK_XCPLITE=ON`, Release, `cortex-m0plus`:
**+1,624 B flash and +4,992 B RAM** over the interim core (42,268 / 44,012 vs
40,644 / 39,020). The ~160 KB figure in upstream's documentation does not
apply here — it describes an Ethernet build with debug prints and A2L upload
compiled in.

## Where the assessment was wrong

**It under-weighted ARMv6-M and over-weighted everything else.** Steps 1–3 were
mechanical. The one genuine defect is a portability bug in the vendored core:

```c
*((uint32_t*)&d0[2]) = (uint32_t)clock;   // xcpLite.c, DAQ timestamp
```

`d0` is the buffer the transport layer returns, so this is a 32-bit store at
offset 2 — unaligned, and therefore a HardFault on Cortex-M0+ rather than a
slow path. It is invisible on every platform upstream tests on. The port
absorbs it by returning a buffer congruent to 2 mod 4 (see `TX_STAGING_SKEW` in
`picodesk_xcp_tl.c`) instead of patching the vendored file. A re-vendor must
re-check that offset.

**It assumed the port would be unverifiable without hardware.** The original
note said steps 2–4 were "hours of uncertain work whose outcome cannot be
validated without hardware anyway". That was too pessimistic about what the
existing rigs could reach: the native suite drives the real library with real
XCP frames through the real CDC framing, and the Renode suite boots the
substituted firmware and exercises the CAL-page switch end to end. What still
genuinely needs a board is the throughput number in CAL-001 (≥50 signals at
100 Hz) — that, and only that, is what O-4 gates.

## Behavioural differences from the interim core

Both are wire-compatible for what the PicoDesk tooling sends, with one
difference in strictness: XCPlite rejects `SET_DAQ_LIST_MODE` unless
`DAQ_MODE_TIMESTAMP` is set (`CRC_CMD_SYNTAX`), where the interim core accepted
mode 0. pyxcp sets the bit by default, so no host change is needed, but a
hand-rolled master would notice.

## Remaining work before the default flips

1. Hardware DAQ-throughput soak on the XCPlite path (O-4): ≥50 signals at
   100 Hz sustained over CDC.
2. Re-run `tools/sizing_report.py` calibration against an XCPlite map so the
   BLD-004 estimate tracks the shipped configuration.
3. Flip `PICODESK_XCPLITE` to `ON` by default and retire `target/xcp/`.
