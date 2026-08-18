#!/usr/bin/env python3
"""Audit the linker map against the BLD-001/BLD-002 memory policy.

Asserts, from the .elf.map produced by the banked linker script:
  - core 0 stack tops SRAM0, core 1 boot stack tops SRAM1
  - .core0_bss / .core1_bss / .rte_shared / .noinit_fault sit in their banks
  - SDK .data/.bss (and SRAM-executed code) sit in SRAM3
  - the fast ISR and fast model step execute from RAM, not XIP flash (BLD-001)

Usage: check_memmap.py <path/to/firmware.elf.map>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SRAM0 = (0x21000000, 0x21010000)
SRAM1 = (0x21010000, 0x21020000)
SRAM2 = (0x21020000, 0x21030000)
SRAM3 = (0x21030000, 0x21040000)
FLASH = (0x10000000, 0x10200000)

#: Fast-path entry points. The spike firmware and generated firmware name
#: theirs differently, so the audit requires at least one ISR to be present
#: and checks every step function it actually finds — rather than demanding
#: symbols from a variant that was not built.
FAST_PATH_ISRS = ["rte_fast_tick_isr", "rte_gen_fast_isr"]
FAST_PATH_STEP_RE = r"(spike_fast_step|pd_\w+_step)"

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


def in_range(addr: int, rng: tuple[int, int]) -> bool:
    return rng[0] <= addr < rng[1]


def main(map_path: str) -> int:
    text = Path(map_path).read_text(encoding="utf-8", errors="replace")

    def symbol(name: str) -> int | None:
        # Matches both plain symbol rows and assignment rows ("name = expr").
        m = re.search(rf"^\s+0x([0-9a-f]+)\s+{re.escape(name)}\b", text, re.MULTILINE)
        if m:
            return int(m.group(1), 16)
        # Static functions only appear as per-function input-section fragments
        # (e.g. ".time_critical.<name>"), possibly wrapped onto the next line.
        m = re.search(
            rf"^\s\.[\w.]+\.{re.escape(name)}\s*\n?\s+0x([0-9a-f]+)", text, re.MULTILINE
        )
        return int(m.group(1), 16) if m else None

    def section(name: str) -> tuple[int, int] | None:
        m = re.search(
            rf"^{re.escape(name)}\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)", text, re.MULTILINE
        )
        return (int(m.group(1), 16), int(m.group(2), 16)) if m else None

    stack_top = symbol("__StackTop")
    check(stack_top == SRAM0[1], f"__StackTop == top of SRAM0 (got {stack_top:#x})"
          if stack_top is not None else "__StackTop present")
    stack1_top = symbol("__StackOneTop")
    check(stack1_top == SRAM1[1], f"__StackOneTop == top of SRAM1 (got {stack1_top:#x})"
          if stack1_top is not None else "__StackOneTop present")

    for name, bank, bank_name in [
        (".core0_bss", SRAM0, "SRAM0"),
        (".core1_bss", SRAM1, "SRAM1"),
        (".noinit_fault", SRAM2, "SRAM2"),
        (".rte_shared", SRAM2, "SRAM2"),
        (".data", SRAM3, "SRAM3"),
        (".bss", SRAM3, "SRAM3"),
    ]:
        sec = section(name)
        if sec is None:
            check(False, f"{name} present in map")
            continue
        addr, size = sec
        check(
            in_range(addr, bank) and addr + size <= bank[1],
            f"{name} [{addr:#x}+{size:#x}] within {bank_name}",
        )

    isrs = [(name, symbol(name)) for name in FAST_PATH_ISRS]
    present_isrs = [(name, addr) for name, addr in isrs if addr is not None]
    check(bool(present_isrs),
          f"a fast-path ISR is present ({' or '.join(FAST_PATH_ISRS)})")
    for name, addr in present_isrs:
        addr &= ~1  # thumb bit
        check(in_range(addr, SRAM3) and not in_range(addr, FLASH),
              f"{name} @ {addr:#x} executes from SRAM, not XIP flash (BLD-001)")

    # Anything declared __not_in_flash_func lands in .time_critical.<sym>;
    # every such section must resolve into SRAM. Driving the check off the
    # section (rather than the symbol name) means slow-rate model steps —
    # which are legitimately allowed to execute from XIP flash — are not
    # swept up, while every fast-loop step is.
    # Section names appear twice in a map: once per input object at address
    # 0x0 (the pre-placement listing) and once at the real VMA. Resolve each
    # through the symbol table instead of trusting the first match.
    section_names = sorted(set(re.findall(
        r"^\s\.time_critical\.([\w.]+)", text, re.MULTILINE)))
    time_critical: list[tuple[str, int]] = []
    misplaced: list[tuple[str, int]] = []
    for name in section_names:
        addr = symbol(name)
        # Address 0 means the section only appears in the pre-placement
        # listing: --gc-sections dropped it because nothing calls it. A
        # discarded function cannot violate a placement rule.
        if addr is None or addr == 0:
            continue
        addr &= ~1  # thumb bit
        time_critical.append((name, addr))
        if not in_range(addr, SRAM3):
            misplaced.append((name, addr))
    for name, addr in misplaced:
        check(False, f"{name} @ {addr:#x} is not SRAM-resident (BLD-001)")
    fast_steps = [name for name, _ in time_critical
                  if re.fullmatch(FAST_PATH_STEP_RE, name)]
    check(bool(time_critical) and not misplaced,
          f"{len(time_critical)} time-critical section(s) in SRAM, "
          f"{len(fast_steps)} of them fast-loop model steps (BLD-001)")

    if failures:
        print(f"\n{len(failures)} memory-policy violation(s)")
        return 1
    print("\nmemory bank audit clean (BLD-001/BLD-002)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
