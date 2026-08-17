"""A2L generation for the generated RTE (MAT-003 / CAL-002 input).

Emits an ASAM MCD-2MC (A2L) file describing the calibration parameters and
measurement signals of a generated workspace. Addresses are emitted as
placeholders and resolved from the ELF's DWARF by a2l_patcher — the linker,
not the generator, decides where symbols land.

The MEMORY_SEGMENT layout mirrors the banked linker script (BLD-002): the
calibration window lives in RAM_SHARED (SRAM2), which is where the CAL pages
sit (RTE-003).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: A2L datatype names for descriptor storage types.
A2L_TYPE = {
    "boolean": "UBYTE", "uint8": "UBYTE", "int8": "SBYTE",
    "uint16": "UWORD", "int16": "SWORD",
    "uint32": "ULONG", "int32": "SLONG",
    "single": "FLOAT32_IEEE", "double": "FLOAT64_IEEE",
}

TYPE_LIMITS = {
    "boolean": (0, 1), "uint8": (0, 255), "int8": (-128, 127),
    "uint16": (0, 65535), "int16": (-32768, 32767),
    "uint32": (0, 4294967295), "int32": (-2147483648, 2147483647),
    "single": (-3.4e38, 3.4e38), "double": (-1.7e308, 1.7e308),
}

#: SRAM2 window: CAL pages + shared RTE data (BLD-002).
RAM_SHARED_BASE = 0x21020000
RAM_SHARED_SIZE = 0x10000

ADDRESS_PLACEHOLDER = 0x00000000


@dataclass(frozen=True)
class A2lItem:
    """One MEASUREMENT or CHARACTERISTIC awaiting a real address."""
    name: str
    kind: str            # "MEASUREMENT" | "CHARACTERISTIC"
    data_type: str
    symbol: str          # DWARF lookup path, e.g. "g_rte_gen_telemetry.heartbeat"
    description: str
    slope: float = 1.0
    bias: float = 0.0


def collect_items(descriptor: dict[str, Any]) -> list[A2lItem]:
    """Measurements for every model outport (via the generated signal stores)
    plus the RTE telemetry block (BLD-003)."""
    items: list[A2lItem] = []
    for name in sorted(descriptor["models"]):
        model = descriptor["models"][name]
        store = f"s_store_{model['rate_group']}"
        for port in model["outports"]:
            items.append(A2lItem(
                name=f"{name}.{port['name']}",
                kind="MEASUREMENT",
                data_type=port["data_type"],
                symbol=f"{store}.{name}_{port['name']}",
                description=f"{name} outport {port['name']} "
                            f"({model['rate_group']})",
                slope=float(port.get("slope", 1.0)),
                bias=float(port.get("bias", 0.0)),
            ))
    for field, desc in (
        ("heartbeat", "fast-loop heartbeat (BLD-007)"),
        ("fast_ticks", "fast-loop tick count"),
        ("overrun_count", "fast-loop overruns (BLD-003)"),
        ("exec_max_us", "worst fast-loop execution time, us"),
        ("dispatch_jitter_max_us", "worst dispatch jitter, us (NFR-1 proxy)"),
        ("seqlock_fault_count", "seqlock stale fallbacks (RTE-004)"),
        ("daq_frames_pushed", "DAQ frames produced (RTE-005)"),
    ):
        items.append(A2lItem(
            name=f"RTE.{field}", kind="MEASUREMENT", data_type="uint32",
            symbol=f"g_rte_gen_telemetry.{field}", description=desc))
    return items


def _compu_method(item: A2lItem) -> str:
    if item.slope == 1.0 and item.bias == 0.0:
        return "NO_COMPU_METHOD"
    return f"CM_{item.name.replace('.', '_')}"


def render_a2l(items: list[A2lItem], *, project: str = "PicoDesk",
               addresses: dict[str, int] | None = None) -> str:
    """Render the A2L. Unresolved symbols get ADDRESS_PLACEHOLDER."""
    addresses = addresses or {}
    out: list[str] = []
    w = out.append

    w("ASAP2_VERSION 1 61")
    w(f'/begin PROJECT {project} "PicoDesk generated workspace"')
    w(f'  /begin MODULE {project} "RP2040 RTE"')
    w('    /begin MOD_COMMON "little endian, byte alignment"')
    w("      BYTE_ORDER MSB_LAST")
    w("      ALIGNMENT_BYTE 1")
    w("      ALIGNMENT_WORD 2")
    w("      ALIGNMENT_LONG 4")
    w("    /end MOD_COMMON")
    w('    /begin MOD_PAR "RP2040 banked layout (BLD-002)"')
    w("      /begin MEMORY_SEGMENT RAM_SHARED")
    w('        "SRAM2: CAL pages, seqlock buses, DAQ ring (RTE-003/004/005)"')
    w(f"        DATA RAM INTERN 0x{RAM_SHARED_BASE:08X} "
      f"0x{RAM_SHARED_SIZE:X} -1 -1 -1 -1 -1")
    w("      /end MEMORY_SEGMENT")
    w("    /end MOD_PAR")

    for item in items:
        addr = addresses.get(item.symbol, ADDRESS_PLACEHOLDER)
        lo, hi = TYPE_LIMITS[item.data_type]
        compu = _compu_method(item)
        if item.kind == "MEASUREMENT":
            w(f"    /begin MEASUREMENT {item.name.replace('.', '_')}")
            w(f'      "{item.description}"')
            w(f"      {A2L_TYPE[item.data_type]} {compu} 0 0 {lo} {hi}")
            w(f"      ECU_ADDRESS 0x{addr:08X}")
            w(f'      SYMBOL_LINK "{item.symbol}" 0')
            w("    /end MEASUREMENT")
        else:
            w(f"    /begin CHARACTERISTIC {item.name.replace('.', '_')}")
            w(f'      "{item.description}"')
            w(f"      VALUE 0x{addr:08X} REC_{item.data_type.upper()} "
              f"0 {compu} {lo} {hi}")
            w(f'      SYMBOL_LINK "{item.symbol}" 0')
            w("    /end CHARACTERISTIC")

    for item in items:
        compu = _compu_method(item)
        if compu == "NO_COMPU_METHOD":
            continue
        w(f'    /begin COMPU_METHOD {compu} "{item.name} scaling"')
        w(f'      LINEAR "%.6f" "" COEFFS_LINEAR {item.slope} {item.bias}')
        w("    /end COMPU_METHOD")

    w("  /end MODULE")
    w("/end PROJECT")
    return "\n".join(out) + "\n"
