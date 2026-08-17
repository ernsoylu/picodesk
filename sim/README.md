# Renode simulation

Whole-firmware system tests on an emulated dual-core RP2040, run in CI on
every push (`sim / Renode system test`). The real spike firmware boots through
a bootrom, starts FreeRTOS SMP on both cores, and the Robot suite asserts on
the firmware's own telemetry stream.

## Stack

- **Renode 1.16.1** (linux-portable) — version pinned by the platform models.
- **[matgla/Renode_RP2040](https://github.com/matgla/Renode_RP2040)** (MIT),
  pinned as the `external/renode-rp2040` submodule: RP2040 peripheral models
  (SIO, timers+alarms, UART, clocks, watchdog, …) compiled from C# source at
  startup — no dotnet build required.
- **`PICODESK_SIM` firmware** (`cmake -DPICODESK_SIM=ON`): identical to the
  hardware build except the XCP task drops its TinyUSB transport — the RP2040
  USB block is not modeled. XCP wire-level behavior is covered by the native
  protocol tests instead (`tests/native/test_xcp_core.c`).

## Local overrides (documented deviations)

- `picodesk_board.repl` maps the non-striped SRAM bank windows
  (0x2100_0000–0x2103_FFFF) that the banked linker script uses; the stock
  platform only maps the striped window. Real `MappedMemory`, because SRAM3
  hosts SRAM-executed code and Renode CPUs execute from mapped memory only.
- `patches/rp2040_sio.cs` fixes upstream spinlock modeling: ownership was
  stored as the CPU slot index, so slot 0 collided with the "free" sentinel
  and both cores could take a FreeRTOS SMP kernel lock simultaneously
  (asserting in `vPortRecursiveLock`). The patch stores owner as slot+1 and
  models real try-claim semantics. Loaded via
  `initialize_peripherals_patched.resc`; drop both when fixed upstream.

## What the suite verifies (`picodesk.robot`)

`picodesk.robot` — the hand-written spike firmware:

| Test | Phase | Requirements |
|---|---|---|
| Boot And 1kHz Fast Dispatch | P0/P1 | SMP boot, RTE-002 (dhb≈1000, ovr=0) |
| Rate Groups And RTE Primitives Healthy | P1/P2 | RTE-002 ratios, RTE-004 (slf=0), RTE-005 (daq>0, drop=0) |
| CAL Page Transactional Switch | P2/P3 | RTE-003 (cal_sw≥1, switched kp active, ovr=0 throughout) |

`picodesk_gen.robot` — firmware produced by `picodesk.rtegen`:

| Test | Phase | Requirements |
|---|---|---|
| Generated RTE Runs The Full Mesh | P5 (M1) | RTE-002/004/005 on generated code; the ASW↔ASW round trip closes through both seqlock buses |

`picodesk_faults.robot` — fault-injection drills:

| Test | Phase | Requirements |
|---|---|---|
| Assert Failure Survives Reset And Is Reported | P6 | BLD-006 (record persists across the watchdog reset, file/line/heartbeat captured) |
| Fault Record Is Consumed After Reporting | P6 | BLD-006 (no stale re-reporting) |
| Wedged Fast Path Trips The Cross-Core Watchdog | P6 | BLD-007 (heartbeat stalls while core 1 stays healthy → reset) |
| Stalled Monitor Task Trips The Watchdog | P6 | BLD-007 (hardware backstop) |
| Healthy System Keeps Feeding The Watchdog | P6 | BLD-007 negative control |

## What emulation deliberately does NOT cover

- **NFR-1 jitter and all timing budgets.** Virtual time is not real time; the
  logic analyzer on hardware is the only truth.
- **USB / XCP transport.** The RP2040 USB block is unmodeled (see the
  `PICODESK_SIM` build above); the XCP protocol core is covered natively.
- **HardFault exception dispatch (BLD-006).** Renode 1.16.1's Cortex-M model
  halts the core with "CPU abort" on a fetch from unmapped memory rather than
  vectoring to the HardFault handler, so the naked handler's stacked-frame
  extraction is a hardware-only gate. Everything downstream of it — record
  write, watchdog reboot, persistence across reset, boot report — is proven
  by the assert drill, which shares that whole path. Three injection
  mechanisms were tried before settling on this split; the two rejected ones
  (unaligned load, undefined instruction) were not trapped at all, which
  would have made the drill silently vacuous.
- **Analog behavior.** ADC/PWM are functional stubs.

## Running locally

```
cmake -S target -B build-sim -G Ninja -DCMAKE_BUILD_TYPE=Release -DPICODESK_SIM=ON
ninja -C build-sim
pip install -r <renode-portable>/tests/requirements.txt
<renode-portable>/renode-test \
    --variable FIRMWARE:$PWD/build-sim/picodesk_firmware.elf sim/picodesk.robot
```

Interactive session: `renode --console -e '$global.FIRMWARE=@build-sim/picodesk_firmware.elf; include @sim/picodesk_sim.resc; showAnalyzer sysbus.uart0; start'`
(≈3–4 host seconds per virtual second).
