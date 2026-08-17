"""Phase 6 tests: A2L generation and DWARF address patching (CAL-002).

The patching tests need a real ELF with DWARF. They resolve symbols against
whichever firmware ELF is present (built by CI before the host tests run)
and skip otherwise, so the suite stays green on a bare checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from picodesk.xcp import a2l
from picodesk.xcp.a2l_patcher import (
    DwarfResolver,
    SymbolNotFoundError,
    patch_a2l,
)

REPO = Path(__file__).parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "gen_ws"
ELF_CANDIDATES = [
    REPO / "build-gen" / "picodesk_gen_firmware.elf",
    REPO / "build-sim" / "picodesk_firmware.elf",
    REPO / "build-a" / "picodesk_firmware.elf",
]


def _find_elf() -> Path | None:
    return next((p for p in ELF_CANDIDATES if p.is_file()), None)


needs_elf = pytest.mark.skipif(_find_elf() is None,
                               reason="no firmware ELF built yet")


@pytest.fixture
def descriptor() -> dict:
    return json.loads((FIXTURES / "descriptor.json").read_text())


# --- generation -------------------------------------------------------------

def test_items_cover_outports_and_telemetry(descriptor) -> None:
    items = a2l.collect_items(descriptor)
    names = {i.name for i in items}
    assert "FastCtrl.torque_cmd" in names
    assert "SlowSense.derate_pct" in names
    assert "RTE.heartbeat" in names
    fast = next(i for i in items if i.name == "FastCtrl.torque_cmd")
    assert fast.symbol == "s_store_fast_1ms.FastCtrl_torque_cmd"


def test_render_emits_ram_shared_segment(descriptor) -> None:
    text = a2l.render_a2l(a2l.collect_items(descriptor))
    assert "ASAP2_VERSION" in text
    # MAT-003: A2L segments match the custom linker (RAM_SHARED = SRAM2).
    assert "MEMORY_SEGMENT RAM_SHARED" in text
    assert f"0x{a2l.RAM_SHARED_BASE:08X}" in text
    assert "/begin MEASUREMENT FastCtrl_torque_cmd" in text
    assert 'SYMBOL_LINK "s_store_fast_1ms.FastCtrl_torque_cmd"' in text


def test_scaling_emits_compu_method() -> None:
    items = [a2l.A2lItem(name="M.p", kind="MEASUREMENT", data_type="int16",
                         symbol="s_store.M_p", description="scaled",
                         slope=0.001, bias=2.0)]
    text = a2l.render_a2l(items)
    assert "COMPU_METHOD CM_M_p" in text
    assert "COEFFS_LINEAR 0.001 2.0" in text


# --- DWARF resolution (CAL-002) ---------------------------------------------

@needs_elf
def test_resolves_plain_global() -> None:
    with DwarfResolver(_find_elf()) as resolver:
        addr = resolver.resolve("g_fault_record")
    # .noinit_fault is the first thing in SRAM2 (BLD-002/BLD-006).
    assert 0x21020000 <= addr < 0x21030000


@needs_elf
def test_resolves_nested_struct_member() -> None:
    """The CAL-002 requirement proper: inner member VMAs, not just the
    struct's base address."""
    with DwarfResolver(_find_elf()) as resolver:
        base = resolver.resolve("g_fault_record")
        kind = resolver.resolve("g_fault_record.kind")
        pc = resolver.resolve("g_fault_record.pc")
    assert kind == base + 4     # magic, kind, pc, ...
    assert pc == base + 8


@needs_elf
def test_unknown_symbol_raises() -> None:
    with DwarfResolver(_find_elf()) as resolver:
        with pytest.raises(SymbolNotFoundError):
            resolver.resolve("no_such_symbol_anywhere")
        with pytest.raises(SymbolNotFoundError):
            resolver.resolve("g_fault_record.no_such_member")


@needs_elf
def test_patch_rewrites_addresses_and_reports_unresolved(tmp_path) -> None:
    items = [
        a2l.A2lItem(name="Fault.kind", kind="MEASUREMENT", data_type="uint32",
                    symbol="g_fault_record.kind", description="fault kind"),
        a2l.A2lItem(name="Fault.pc", kind="MEASUREMENT", data_type="uint32",
                    symbol="g_fault_record.pc", description="fault pc"),
        a2l.A2lItem(name="Ghost.sig", kind="MEASUREMENT", data_type="uint32",
                    symbol="definitely_not_a_symbol", description="missing"),
    ]
    src = tmp_path / "gen.a2l"
    src.write_text(a2l.render_a2l(items), encoding="utf-8")
    assert src.read_text().count("ECU_ADDRESS 0x00000000") == 3

    out = tmp_path / "patched.a2l"
    result = patch_a2l(_find_elf(), src, out)

    assert result["patched"] == 2
    assert result["unresolved"] == ["definitely_not_a_symbol"]
    text = out.read_text()
    # Resolved entries carry real SRAM2 addresses; the missing one is left
    # at its placeholder rather than silently faked.
    assert text.count("ECU_ADDRESS 0x00000000") == 1
    assert "ECU_ADDRESS 0x2102" in text
