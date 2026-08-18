# Hardware campaign — closing the six gates emulation cannot

Six requirements are verified as far as host testing and Renode can reach and
no further (O-1 … O-6 in [PROBLEM_INVENTORY.md](PROBLEM_INVENTORY.md)). This is
the runbook for closing them on a physical RP2040: what to wire, what to build,
what to run, and what artefact each gate produces.

The analysis is already written and unit-tested against synthetic captures
(`tests/test_hil_analyze.py`), including cases that must *fail*. Nothing here
should need inventing on the day.

## Bench setup

| Item | Note |
|---|---|
| Raspberry Pi Pico | RP2040, stock 125 MHz |
| Logic analyzer | ≥ 2 channels, ≥ 8 MS/s, deep enough for 1,000 s of edges — a 24 MHz 8-channel clone is sufficient at 1 MS/s |
| USB cable | data-capable, for CDC and power |
| UART adapter | GPIO 0/1 at 115200 8N1, for the telemetry stream |

Probe points, all defined in `target/rte/rte_dispatch.c`:

| GPIO | Signal | Used by |
|---|---|---|
| 2 | high for the duration of the fast ISR | O-1 (rising edges), ISR execution time (high time) |
| 3 | pulses on a detected overrun | O-1 (must stay idle) |

Ground the analyzer to a Pico GND pin next to the probed pins, not across the
board — a long ground return adds edge noise at the microsecond scale the
measurements care about.

## O-1 · NFR-1 dispatch jitter ≤ 50 µs at p99.99

```bash
cmake -S target -B build-hw -DCMAKE_BUILD_TYPE=Release
cmake --build build-hw
# flash build-hw/picodesk_firmware.uf2
```

Capture GPIO 2 and GPIO 3 for **1,100 seconds** at 1 MS/s or better — the
requirement is 1,000,000 cycles at 1 kHz, and the margin covers trimming the
start and end. Export as CSV with a header row naming the channels.

Run it twice: once idle, once under a full XCP DAQ load (see O-4 below), since
the requirement says "under full XCP DAQ load".

```bash
python tools/hil_analyze.py jitter capture_loaded.csv \
    --channel D2 --period-us 1000 --json artefacts/nfr1_loaded.json
```

Exit 0 is a pass. Exit 1 with `INCONCLUSIVE` means the capture is too short for
a p99.99 claim — the tool will not issue a pass below 1,000,000 samples, which
is the point.

**Read the `period us` line.** The tool fits the dispatch period rather than
assuming 1000.000 µs, because the Pico's crystal and the analyzer's timebase
are independent and a 50 ppm difference accumulates 50 µs of phase over a
1-second window — an entire budget's worth of jitter the system does not have.
If the fitted period differs from nominal by more than 100 ppm the tool says
so; check the analyzer before trusting anything else on the page.

Also confirm GPIO 3 never pulses, and that the telemetry line reports `ovr=0`.

**Artefact:** `artefacts/nfr1_idle.json`, `artefacts/nfr1_loaded.json`, plus
the raw captures.

## O-2 · NFR-3 no critical section over 15 µs

Two complementary measurements; neither alone is sufficient.

**Continuous, coarse — the regression signal.** Build with the probe on:

```bash
cmake -S target -B build-probe -DCMAKE_BUILD_TYPE=Release -DPICODESK_NFR3_PROBE=ON
cmake --build build-probe
```

The telemetry line then carries `crit_max=<us> crit_n=<samples>`. Read both:
`crit_max=0` with `crit_n` climbing means every seqlock write finished inside
one 1 µs tick, which is the expected healthy result. `crit_n=0` means the probe
is not in the path — treat that as a broken build, not a good result. The probe
only sees the sections PicoDesk owns; FreeRTOS SMP and SDK sections are out of
its reach, which is why the second measurement exists.

**Scoped, precise — the verification.** Instrument the specific section under
suspicion with a GPIO toggle, capture it, and:

```bash
python tools/hil_analyze.py pulse capture_crit.csv \
    --channel D5 --budget-us 15 --json artefacts/nfr3_<section>.json
```

Sections worth probing, in order of risk: the FreeRTOS SMP kernel spinlock
(`vPortRecursiveLock`), `save_and_disable_interrupts()` in SDK flash routines,
and any HAL call a fast-loop model reaches through `hal_manifest.json`.

**Artefact:** one JSON per probed section, plus a telemetry capture showing
`crit_max`/`crit_n` over a long run.

## O-3 · RTE-004 24 h two-core soak

Run the spike firmware for 24 hours with the UART telemetry logged, then check
that `slf` (seqlock stale fallbacks) stayed at 0 and `daq_drop` at 0 across the
whole log:

```bash
python - <<'PY'
import re, sys
bad = [l for l in open("soak.log")
       if (m := re.search(r"slf=(\d+).*daq_drop=(\d+)", l))
       and (int(m.group(1)) or int(m.group(2)))]
print(f"{len(bad)} bad lines"); print(*bad[:5], sep="")
PY
```

A torn read that the bounded retry did not catch would show as a `slf`
increment; the requirement is that the fallback path is never needed under
normal operation, not merely that it works.

**Artefact:** the full 24 h `soak.log`.

## O-4 · CAL-001 sustained ≥ 50 signals at 100 Hz over USB CDC

This is the gate that also decides whether `PICODESK_XCPLITE` becomes the
default, so run it on the XCPlite build:

```bash
cmake -S target -B build-xcp -DCMAKE_BUILD_TYPE=Release -DPICODESK_XCPLITE=ON
cmake --build build-xcp
python tests/hil/xcp_smoke.py --port /dev/ttyACM0 --seconds 300 \
    --cal-base 0x<from the .map> --frame-base 0x<from the .map>
```

Get the two addresses from `build-xcp/picodesk_firmware.elf.map`: the CAL
window base and `g_xcp_daq_frame_marker`.

Run the same against the default interim build for comparison. If XCPlite holds
the rate, flip the CMake default and delete `target/xcp/` (see
[XCPLITE_FEASIBILITY.md](XCPLITE_FEASIBILITY.md)).

**Artefact:** the smoke-test output for both builds.

## O-5 · GUI-012 MDF4 recording from a live DAQ stream

Depends on O-4. With DAQ streaming, record through the GUI's Live Tuning panel,
then round-trip the file:

```bash
python - <<'PY'
from asammdf import MDF
m = MDF("recording.mf4")
print(m.channels_db.keys())
print(m.get("torque_cmd").samples[:10])
PY
```

Check the channel set matches the configured DAQ list and the timestamps are
monotonic at the configured rate.

**Artefact:** `recording.mf4` plus the round-trip output.

## O-6 · BLD-006 HardFault exception dispatch

Renode halts the core with "CPU abort" instead of vectoring, so this one step —
and only this step — is unverified. Everything downstream of the vector (record
write, watchdog reboot, persistence across reset, boot report) is already
proven by the assert drill in `sim/picodesk_faults.robot`, which shares that
path.

On hardware, trigger the injection over the fault console and confirm the boot
report after the watchdog reset:

```
PicoDesk RTE spike boot
FAULT kind=<n> pc=<hex> lr=<hex> hb=<n> wdt=<n>
```

Then confirm the same record survives a power cycle, which is what
`.noinit_fault` exists for.

**Artefact:** the UART log spanning injection, reset and boot report.

## Not on this bench

O-8 (Qt/PyQt licensing) was never a hardware gate and is now decided: GPL-3.0,
with PicoDesk itself GPL-3.0-or-later. See
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

## Closing out

After each gate, move its entry in `tools/traceability.yaml` from
`{kind: hardware, note: ...}` to a `{kind: file, path: artefacts/...}` citing
the archived result, and re-run:

```bash
python tools/traceability.py --markdown docs/TRACEABILITY.md
```

The matrix verifies that every artefact it cites exists, so a gate cannot be
marked closed against a file that was never archived. When it reports zero
hardware gates, v1.0 is tellable.
