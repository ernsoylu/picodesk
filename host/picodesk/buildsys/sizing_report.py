"""Static SRAM/Flash footprint estimation before CMake runs (BLD-004).

The estimate is built from the descriptor + routing alone — no compiler
involved — so the GUI can halt a workspace that cannot possibly fit before
paying for a build. Ceilings are hard: exceeding either one stops the build.

Accuracy is kept honest by `compare_with_map()`, which measures the estimate
against the linker map after a real build; the per-model/per-signal constants
below are calibrated from those comparisons.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SRAM_LIMIT_BYTES = 200 * 1024
FLASH_LIMIT_BYTES = 1536 * 1024

#: Physical banks (BLD-002). SRAM3 holds SDK data/bss and SRAM-executed code.
BANK_SIZE_BYTES = 64 * 1024

TYPE_SIZE = {
    "boolean": 1, "int8": 1, "uint8": 1,
    "int16": 2, "uint16": 2,
    "int32": 4, "uint32": 4, "single": 4,
    "double": 8,
}

# --- calibrated constants ---------------------------------------------------
# Measured against generated-firmware linker maps (see compare_with_map and
# tools/calibrate_sizing.py). Runtime overhead is NOT split evenly across
# banks: the FreeRTOS heap plus the idle/timer stacks live in SRAM2 (BLD-002),
# while SRAM3 carries only SDK .data/.bss and SRAM-executed code.
RUNTIME_SRAM2_BASE = 19968   # 16 kB kernel heap + idle/timer stacks + telemetry
RUNTIME_SRAM3_BASE = 5888    # SDK data/bss + __not_in_flash_func code
CORE0_STACK_BYTES = 2048     # core 0 main/ISR stack in SRAM0
RUNTIME_FLASH_BASE = 72 * 1024

TASK_STACK_BYTES = 2 * 1024          # BLD-005 budget per rate-group task
FIXED_TASKS = 4                      # daq, stats, watchdog, idle/timer share
SEQLOCK_BYTES = 4                    # sequence word per bus
PER_MODEL_FLASH = 512                # step/init code, conservative
PER_SIGNAL_FLASH = 24                # copy-in/publish code per routed signal
DAQ_RING_FRAMES = 256                # rte_gen.c


@dataclass
class SizingReport:
    sram_total: int = 0
    flash_total: int = 0
    banks: dict[str, int] = field(default_factory=dict)
    per_model: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def sram_ok(self) -> bool:
        return self.sram_total <= SRAM_LIMIT_BYTES

    @property
    def flash_ok(self) -> bool:
        return self.flash_total <= FLASH_LIMIT_BYTES

    @property
    def ok(self) -> bool:
        return self.sram_ok and self.flash_ok and all(
            v <= BANK_SIZE_BYTES for v in self.banks.values())

    def blocking_reasons(self) -> list[str]:
        reasons = []
        if not self.sram_ok:
            reasons.append(
                f"SRAM estimate {self.sram_total / 1024:.1f} kB exceeds the "
                f"{SRAM_LIMIT_BYTES / 1024:.0f} kB budget (BLD-004)")
        if not self.flash_ok:
            reasons.append(
                f"Flash estimate {self.flash_total / 1024:.1f} kB exceeds the "
                f"{FLASH_LIMIT_BYTES / 1024:.0f} kB budget (BLD-004)")
        for bank, used in sorted(self.banks.items()):
            if used > BANK_SIZE_BYTES:
                reasons.append(
                    f"{bank} estimate {used / 1024:.1f} kB exceeds the 64 kB "
                    f"bank (BLD-002)")
        return reasons

    def format_text(self) -> str:
        lines = ["Static sizing estimate (BLD-004)", ""]
        for bank in sorted(self.banks):
            used = self.banks[bank]
            lines.append(f"  {bank:<8} {used / 1024:8.1f} kB / 64.0 kB "
                         f"({100 * used / BANK_SIZE_BYTES:5.1f}%)")
        sram_line = (f"  SRAM     {self.sram_total / 1024:8.1f} kB / "
                     f"{SRAM_LIMIT_BYTES / 1024:.0f} kB")
        flash_line = (f"  Flash    {self.flash_total / 1024:8.1f} kB / "
                      f"{FLASH_LIMIT_BYTES / 1024:.0f} kB")
        verdict = "PASS" if self.ok else "BUILD HALTED"
        lines += ["", sram_line, flash_line, "", f"  verdict: {verdict}"]
        lines += [f"    - {r}" for r in self.blocking_reasons()]
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def _port_bytes(port: dict[str, Any]) -> int:
    return TYPE_SIZE[port["data_type"]] * int(port.get("width", 1))


def estimate_footprint(descriptor: dict[str, Any],
                       routing: dict[str, Any] | None = None) -> SizingReport:
    """Estimate per-bank SRAM and flash for a workspace."""
    models = descriptor["models"]
    routing = routing or {"connections": []}

    report = SizingReport()

    # Signal stores live in the executing core's bank; the fast group is
    # core 0 (SRAM0), slower groups are core 1 (SRAM1).
    store_fast = store_slow = 0
    daq_frame = 4  # tick
    flash = RUNTIME_FLASH_BASE
    slow_groups: set[str] = set()

    for name in sorted(models):
        model = models[name]
        out_bytes = sum(_port_bytes(p) for p in model["outports"])
        in_bytes = sum(_port_bytes(p) for p in model["inports"])
        if model["rate_group"] == "fast_1ms":
            store_fast += out_bytes
            daq_frame += out_bytes
        else:
            store_slow += out_bytes
            slow_groups.add(model["rate_group"])
        model_flash = PER_MODEL_FLASH + PER_SIGNAL_FLASH * (
            len(model["inports"]) + len(model["outports"]))
        flash += model_flash
        report.per_model[name] = out_bytes + in_bytes

    # Cross-rate connections become seqlock buses in SRAM2, each with a
    # reader shadow in the consumer's bank (RTE-004).
    bus_bytes: dict[tuple[str, str], int] = {}
    for conn in routing["connections"]:
        prod, cons = conn["producer"], conn["consumer"]
        pm, pp = prod.split(".", 1)
        cm = cons.split(".", 1)[0]
        if pm not in models or cm not in models:
            continue  # HAL endpoint: no shared storage
        pg, cg = models[pm]["rate_group"], models[cm]["rate_group"]
        if pg == cg:
            continue
        port = next((p for p in models[pm]["outports"] if p["name"] == pp), None)
        if port is None:
            continue
        bus_bytes[(pg, cg)] = bus_bytes.get((pg, cg), 0) + _port_bytes(port)

    shared = sum(bus_bytes.values()) + SEQLOCK_BYTES * len(bus_bytes)
    daq_frame = (daq_frame + 3) // 4 * 4  # C struct word alignment
    shared += DAQ_RING_FRAMES * daq_frame
    for (_, dst), size in bus_bytes.items():
        if dst == "fast_1ms":
            store_fast += size
        else:
            store_slow += size

    task_count = len(slow_groups) + FIXED_TASKS
    report.banks = {
        "SRAM0": store_fast + CORE0_STACK_BYTES,
        "SRAM1": store_slow + task_count * TASK_STACK_BYTES,
        "SRAM2": shared + RUNTIME_SRAM2_BASE,
        "SRAM3": RUNTIME_SRAM3_BASE,
    }
    report.sram_total = sum(report.banks.values())
    report.flash_total = flash

    if not report.ok:
        report.notes.append("build halted before CMake was invoked")
    return report


def parse_map_usage(map_path: Path) -> dict[str, int]:
    """Actual per-bank usage from a linker map, for calibration."""
    text = map_path.read_text(encoding="utf-8", errors="replace")
    banks = {"SRAM0": 0, "SRAM1": 0, "SRAM2": 0, "SRAM3": 0}
    ranges = {
        "SRAM0": (0x21000000, 0x21010000),
        "SRAM1": (0x21010000, 0x21020000),
        "SRAM2": (0x21020000, 0x21030000),
        "SRAM3": (0x21030000, 0x21040000),
    }
    for match in re.finditer(
            r"^(\.[\w.]+)\s+0x([0-9a-f]{16})\s+0x([0-9a-f]+)", text, re.MULTILINE):
        addr = int(match.group(2), 16)
        size = int(match.group(3), 16)
        for bank, (lo, hi) in ranges.items():
            if lo <= addr < hi:
                banks[bank] += size
                break
    return banks


def compare_with_map(report: SizingReport, map_path: Path) -> dict[str, Any]:
    """Estimate vs. linker truth. `within_tolerance` is the BLD-004 exit
    criterion (±10 % on the total)."""
    actual = parse_map_usage(map_path)
    actual_total = sum(actual.values())
    error = ((report.sram_total - actual_total) / actual_total
             if actual_total else 0.0)
    return {
        "estimated": report.banks,
        "actual": actual,
        "estimated_total": report.sram_total,
        "actual_total": actual_total,
        "relative_error": error,
        "within_tolerance": abs(error) <= 0.10,
    }
