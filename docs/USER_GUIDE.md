# PicoDesk user guide

PicoDesk turns a directory of independent Simulink models into deterministic
dual-core RP2040 firmware, then lets you tune it live over XCP. This guide
covers the path from a fresh checkout to a running, calibrated target.

## 1. Install the toolchain

| Tool | Version | Why pinned |
|---|---|---|
| ARM GNU toolchain | **12.2.rel1** exactly | Dictates the DWARF format the A2L patcher parses (CAL-002). |
| CMake | ≥ 3.20 | Required by the Pico SDK. |
| Ninja | any | Build speed; the NFR-2 budget assumes it. |
| Python | 3.9–3.11 | Must match your MATLAB release's engine bindings. |
| MATLAB + Simulink + Embedded Coder | R2023b–R2024b | Model extraction and ERT code generation. |

```bash
git clone --recurse-submodules <repo> && cd picodesk
pip install -e "host[dev]"
python -m picodesk.buildsys.dependency_checker      # GUI-004, same check the GUI runs
```

The dependency check is the authority: if it reports a missing required
tool, the GUI disables builds and says which one. Install the MATLAB engine
matching your release (`cd <matlabroot>/extern/engines/python && pip install .`).

## 2. Launch

```bash
picodesk                      # or: python -m picodesk.app
```

The window has four destinations, in the order you will use them.

## 3. Models

**Import Models…** points at a directory of `.slx` files. Each is
content-hashed and extracted through a persistent MATLAB session, so
re-scanning an unchanged workspace costs no engine round trips.

The STATE column answers "can this build right now?":

| State | Meaning | Action |
|---|---|---|
| Fresh | extracted, hash matches | — |
| Stale — re-export | the `.slx` or an attached data dictionary changed since extraction (GUI-001) | Re-scan |
| Blocked · MAT-002 | a fast-loop model uses `single`/`double` | Convert to fixed-point, or move the model to a slower rate group |
| Blocked · assign rate | the model is rate-agnostic (everything inherited) | Pick a rate group in the GROUP column |
| Interface mismatch | a port contradicts the type its data dictionary declares | Fix the model or the dictionary — the mismatch will otherwise surface as a bind refusal |

**Rate-agnostic models.** A model whose sample times are all inherited
(`FixedStepAuto`) carries no rate of its own — the normal shape of the
interface-first development cycle, where rates are an integration decision.
Such models show **UNASSIGNED** with a picker in the GROUP column; the choice
is saved in the routing config (the same model can run in a different slot in
another workspace) and is *forced* at code generation, so the generated step
matches its dispatcher slot exactly. MAT-002 runs against the assigned group:
a `single`-carrying model is refused for the fast group at the moment you
pick it.

**Data dictionaries.** Models attached to `.sldd` dictionaries — including
chains of referenced dictionaries shared between models — extract
transparently; the dictionary files count toward staleness, so editing a
shared interface dictionary re-extracts every model attached to it.

MAT-002 is not advisory. The RP2040 has no FPU, so software floating point
in a 1 ms loop would blow the deadline; the build is refused rather than
producing firmware that misses it.

## 4. Routing

Three panes: **Producers**, **Connections**, **Consumers**. Select a
producer and the consumer pane filters itself in place:

- ports whose type, width or fixed-point scaling differ are greyed out with
  the reason (GUI-008) — no silent coercion;
- already-bound inports show 🔒 and their writer, because an inport takes
  exactly one producer (GUI-009);
- a HAL function that is not ISR-safe cannot be bound to a fast-loop model
  (GUI-006).

Double-click a compatible consumer to bind; double-click a connection to
unlink. Deleting a model unlinks everything it fed.

Any model's outport can feed any other model's inport, in either direction —
the bus is a full mesh, not a hierarchy. Edges between rate groups carry a
**RATE TRANSITION · ZOH / SEQLOCK** badge (GUI-010): they are published
through a bounded seqlock, and the reader falls back to last-known-good data
rather than blocking the fast loop.

**Suggest Bindings** proposes exact name+type matches as a preview you can
apply or undo in one step. Ambiguous matches are deliberately omitted.

**HAL endpoints and physical units.** GPIO endpoints are `boolean` — a pin
is one bit, and boolean button/LED ports bind them directly. The ADC endpoint
is deliberately `uint16` raw counts: converting counts to physical units
(°C, rpm) costs float math, which is banned from the fast loop (MAT-002).
The idiom is a **conditioning model** — a small slow-group model that reads
`hal_adc_read`, applies the scaling, and publishes the physical-units signal
for the rest of the mesh:

```
hal_adc_read ──uint16──▶ CondTemp (slow) ──single °C──▶ consumers
```

UART-sourced signals (e.g. an external sensor feed) have no HAL endpoint
yet: UART0 carries stdio/telemetry, and a second-channel design is tracked
as open work — declare the signal in your interface dictionary, but it
cannot be routed in this version.

## 5. Build

The build button stays disabled — and says why — until the toolchain is
complete, no model is MAT-002 blocked, and the static sizing estimate fits.

The sizing report (BLD-004) estimates per-bank usage *before* CMake runs:

- **SRAM0** core 0 stack and fast-path state
- **SRAM1** FreeRTOS task stacks
- **SRAM2** seqlocks, DAQ ring, CAL pages, kernel heap
- **SRAM3** SDK data and all SRAM-executed code

Ceilings are hard: 200 kB SRAM, 1.5 MB flash.

Successive builds of an unchanged workspace produce byte-identical `.uf2`
files (BLD-008); the reproducibility chip reports the hash.

Flash the resulting `.uf2` by holding BOOTSEL while connecting the Pico and
copying the file to the mass-storage device that appears.

## 6. Calibration

Connect over USB CDC. The telemetry tiles show live health: ISR utilisation,
overruns, seqlock faults, DAQ throughput, watchdog state.

Calibration is **transactional** (RTE-003). Edits go to the *offline* page
and are invisible to the running loop; the banner shows how many changes are
pending. **Switch Page** arms the swap, and the fast ISR commits it at a
`model_step` boundary — so a multi-parameter change lands as one atomic set,
never half-applied mid-step.

Select DAQ signals and **Record MDF4** to log to an ASAM MDF4 file.

## 7. When something goes wrong

Debugging is by telemetry, not a debugger — on-target GDB/OpenOCD is out of
scope for v1.0 (SRS §1.3), because SMP halting is unreliable.

- **The diagnostic console** streams MATLAB, generator and CMake output with
  errors and warnings colour-coded and `file:line` clickable (GUI-005).
- **A boot-time fault report** prints any surviving post-mortem record
  (BLD-006): `FAULT kind=… pc=… lr=… core=… hb=… boot_wdt=…`. `kind=1` is a
  HardFault with the stacked PC; `kind=2` an assert with file pointer and
  line. The record survives the watchdog reset that follows.
- **Unexpected reboots with `boot_wdt=1` and no fault record** mean the
  cross-core watchdog fired (BLD-007): core 0's fast path stopped advancing
  its heartbeat while core 1 stayed healthy. Look for a fast-loop overrun or
  a blocking call that reached the ISR.
- **Rising seqlock faults** mean readers are losing races with a writer and
  falling back to stale data — check whether a cross-rate signal group grew
  past the seqlock payload bound.

## 8. Known limits in v1.0

Parameters revert to compiled defaults on power cycle (no NvM). XCP has no
seed/key protection — lab use only. Host support is Windows/Linux x64;
Apple Silicon is out of scope. There is no host-side SIL emulation.
