# PicoDesk

Python/MATLAB toolchain that turns a batch of independent Simulink models into deterministic
dual-core RP2040 firmware: a PyQt6 GUI routes model signals over a Virtual Functional Bus (VFB),
generates a multi-rate RTE in C, builds with the Pico SDK, and performs live XCP calibration/DAQ
over USB CDC.

The authoritative spec is **[REQUIREMENTS.md](REQUIREMENTS.md)** (SRS v7.1). Every feature and
design decision must trace to a requirement ID (e.g. `RTE-004`, `NFR-3`). Reference these IDs in
commit messages, code comments on safety-critical sections, and test names.

## Architecture (fixed — do not redesign)

Two toolchain sides, one firmware target:

- **Host side (Python 3.9–3.12):** PyQt6 GUI, persistent `matlab.engine` session, `.slx` hash-gated
  batch extraction to a monolithic JSON descriptor, Jinja-style RTE code templating, CMake
  orchestration, `pyelftools` DWARF post-processing for A2L, `pyxcp` + `asammdf` for tuning/logging.
- **Target side (RP2040, Cortex-M0+, 264 kB SRAM, no FPU):**
  - **Core 0 = fast path.** Hardware timer alarm ISR runs the 1 ms rate group entirely from SRAM.
    FreeRTOS SMP idle runs on Core 0 but the fast loop preempts it via hardware IRQ.
  - **Core 1 = slow path.** FreeRTOS (SMP port) tasks for slower rate groups, XCPlite slave over
    USB CDC-ACM, and the watchdog task.
  - Memory banks are deliberately separated (BLD-002): Core 0 stack/BSS → `SRAM0`, Core 1 → `SRAM1`,
    all shared RTE data (seqlocks, DAQ ring, CAL pages, FreeRTOS heap) → `SRAM2` via custom `.ld`.
- **VFB topology — full mesh:** any ASW model's outport can bind any other ASW model's inport, in
  either direction — every model can be producer and consumer simultaneously — in addition to
  ASW ↔ HAL endpoints. The routing layer, RTE generator, and GUI must all handle model→model
  edges as first-class: single writer per inport (GUI-009), exact type/dimension/scaling match
  (GUI-008), and ZOH + bounded-seqlock rate transitions on cross-rate/cross-core edges
  (RTE-004, GUI-010) apply to every edge kind equally.

## Non-negotiable invariants

These are hard requirements; violating any of them is a bug even if the code "works":

1. **No floating point in the fast loop** (MAT-002). RP2040 has no FPU. Any `single`/`double` in a
   fast-loop model is a *hard build error* in the toolchain, and no generated or handwritten C on
   the Core 0 path may introduce software-float calls.
2. **Fast path executes from SRAM** (BLD-001). The fast ISR, every fast `model_step()`, and RTE
   copy routines must carry `__not_in_flash_func()`. Never let fast-path code fetch from XIP flash.
3. **15 µs global critical-section budget** (NFR-3). No critical section on *any* core may hold the
   FreeRTOS SMP spinlock or mask interrupts longer than 15 µs. Prefer lock-free (seqlock) designs;
   never take the kernel spinlock from the Core 0 ISR.
4. **Cross-core data: seqlocks with bounded retry** (RTE-004). Signals >32 bits crossing cores use
   seqlocks in both directions. Writers disable local IRQs only; readers retry at most 3 times then
   fall back to last-known-good. All routed signals initialize to zero/producer defaults at boot.
5. **Calibration via XCP CAL pages, swapped only at step boundary** (RTE-003). Core 1 writes an
   offline RAM page; Core 0 flips the active-page pointer exclusively at the `model_step()`
   boundary. Never write live parameters mid-step.
6. **DAQ frames go through the SRAM2 ring** (RTE-005). The fast ISR snapshots coherent DAQ frames
   into the ring; the Core 1 XCPlite task drains it. No direct fast-path USB access, no torn frames.
7. **Implicit communication** (RTE-001). `model_step()` never reads shared buffers directly — RTE
   copy-in to task-private shadow buffers first.
8. **Reproducible builds** (BLD-008). `-ffile-prefix-map` enforced, `__DATE__`/`__TIME__` banned.
   Rebuilding an unchanged model must produce a bit-identical `.uf2` (SHA-256 verified).
9. **Symbol hygiene** (MAT-003). All generated symbols strictly prefixed per model — 30 models must
   coexist without collisions.
10. **Budget ceilings** (BLD-004): 200 KB SRAM / 1.5 MB Flash estimated pre-build; the build halts
    above these.

## Concurrency model cheat sheet

- Fastest rate: Core 0 hardware timer alarm ISR (not a FreeRTOS task).
- Slower rates: Core 1 FreeRTOS tasks, released via `vTaskNotifyGiveFromISR()` from the Core 0 ISR
  (RTE-002). Priorities: rate-monotonic for rate groups, XCP lowest (BLD-005). Default 2 KB stacks;
  high-water marks are telemetered.
- Watchdog: Core 0 ISR increments a heartbeat; a Core 1 task verifies advancement before feeding
  the hardware watchdog (BLD-007). HardFaults capture PC/LR into a reserved noinit RAM section that
  survives watchdog reset (BLD-006).

## Toolchain versions (pinned — see REQUIREMENTS.md §8 for full matrix)

MATLAB R2025b (ERT), Python 3.9–3.12 (must match MATLAB Engine), PyQt6/Qt 6.5+,
Pico SDK ≥ 1.5.1, FreeRTOS Kernel ≥ 11.1.0 (SMP), ARM GCC 12.2.rel1, CMake ≥ 3.20,
Vector XCPlite (MIT — retain license headers), pyxcp ≥ 0.21, pyelftools ≥ 0.31,
asammdf ≥ 7.4. Host platforms: Windows/Linux x64 only.

## Out of scope for v1.0 — do not build these

On-target GDB/OpenOCD debugging, NvM parameter persistence, Apple Silicon support, SIL host
emulation, XCP seed/key security. If a task seems to need one of these, flag it instead of
implementing it.

## Repository layout

```
picodesk/
├── REQUIREMENTS.md          # SRS v7.1 — authoritative spec
├── host/                    # Python toolchain — pip install -e "host[dev]"
│   ├── pyproject.toml
│   └── picodesk/
│       ├── app.py           # entry point (picodesk console script)
│       ├── gui/             # PyQt6 (routing matrix, diagnostic console, XCP dashboard)
│       ├── matlab_bridge/   # persistent matlab.engine session, .slx extraction, hash gating
│       ├── rtegen/          # RTE C-code templating (Jinja2) from routing JSON
│       ├── buildsys/        # dependency checker, sizing report, CMake driver
│       └── xcp/             # pyxcp master, A2L DWARF patching, MDF4 logging
├── target/                  # C firmware, built via CMake + Pico SDK
│   ├── CMakeLists.txt
│   ├── src/                 # main.c, HardFault capture
│   ├── rte/                 # dispatcher, seqlocks, CAL pages, DAQ ring
│   ├── hal/                 # default HAL (GPIO/ADC/PWM) + hal_manifest.json
│   ├── xcp/                 # interim protocol core (default until CAL-001 soak)
│   ├── xcplite/             # vendor/ = unmodified XCPlite V6.4; port/ = CDC transport,
│   │                        #   platform shim, ApplXcp* bound to the RTE CAL pages
│   ├── config/              # FreeRTOSConfig.h (SMP)
│   └── ld/                  # custom bank-separated linker script
└── tests/                   # host-side pytest suite
```

Note: the build-orchestration package is `buildsys/`, not `build/` — a `build/` path
would collide with setuptools/CMake build-output directories and .gitignore rules.
Keep this section current as the structure evolves.

## Working conventions

- Host code: Python 3.9-compatible syntax (the floor of the supported range), type hints throughout, PyQt6 UI work
  off the main thread for MATLAB/CMake calls (GUI-002/GUI-005 demand a responsive UI).
- Target code: C11, Pico SDK idioms. Anything on the fast path gets scrutiny against invariants
  1–3 above before merge.
- Routing configs are versioned JSON with schema migration (GUI-003) — never break the schema
  silently; bump the version and add a migration.
- Debugging the target happens through XCP/MDF4 and telemetry only — don't reach for OpenOCD/GDB.
