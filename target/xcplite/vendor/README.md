# Vendored Vector XCPlite

Upstream: https://github.com/vectorgrp/XCPlite — **tag V6.4**, MIT licensed
(see `LICENSE`; the exact commit is in `UPSTREAM_COMMIT`).

`xcpLite.c`, `xcpLite.h` and `xcp.h` are **unmodified**. Everything PicoDesk
supplies — the umbrella header, configuration, platform shim and the
CDC transport — lives in `../port/`, so re-vendoring a newer upstream is a
file copy rather than a merge.

## Why V6.4 and not v2.x

The v2.x line (`vectorgrp/XCPlite` master) is XCP **on Ethernet**: its
`platform.h` hard-errors with *"32 Bit *X OS currently not supported"* on any
32-bit non-Windows target, so it cannot build for Cortex-M0+ at all without
forking the platform layer. V6.4 is the classic embedded line and compiles
for `cortex-m0plus` once the integrator supplies `main.h` and the `*_cfg.h`
set, which is its documented seam.

## Cost as integrated

Whole-firmware delta from switching `-DPICODESK_XCPLITE=ON` (Release,
`cortex-m0plus`), measured against the interim core it replaces:

| | flash | RAM |
|---|---|---|
| interim core | 40,644 B | 39,020 B |
| vendored XCPlite | 42,268 B | 44,012 B |
| **delta** | **+1,624 B** | **+4,992 B** |

Nowhere near the ~160 KB the v2.x documentation quotes, because debug prints
and A2L upload are compiled out (`port/main_cfg.h`) and the DAQ table is sized
for this target rather than an Ethernet MTU (`port/xcp_cfg.h`).

## One portability defect worth knowing about

`xcpLite.c` writes the DAQ timestamp as `*((uint32_t*)&d0[2]) = clock` — a
32-bit store at offset 2 of the buffer the transport layer hands it. Cortex-M0+
has no unaligned access, so that is a HardFault, not a slow path. Upstream
never sees it because its own transports run on architectures that fix up
unaligned accesses.

Rather than patch the vendored file, `port/picodesk_xcp_tl.c` returns a buffer
whose address is congruent to 2 mod 4, which makes the store land aligned. If
you re-vendor a newer upstream, check whether that offset has moved.
