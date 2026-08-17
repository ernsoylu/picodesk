"""Phase 6 tests: sizing estimation (BLD-004) and build-output parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from picodesk.buildsys import sizing_report as sz
from picodesk.buildsys.cmake_driver import parse_diagnostic

FIXTURES = Path(__file__).parent / "fixtures" / "gen_ws"


@pytest.fixture
def descriptor() -> dict:
    return json.loads((FIXTURES / "descriptor.json").read_text())


@pytest.fixture
def routing() -> dict:
    return json.loads((FIXTURES / "routing.json").read_text())


# --- sizing (BLD-004) -------------------------------------------------------

def test_small_workspace_fits(descriptor, routing) -> None:
    report = sz.estimate_footprint(descriptor, routing)
    assert report.ok
    assert report.blocking_reasons() == []
    assert set(report.banks) == {"SRAM0", "SRAM1", "SRAM2", "SRAM3"}
    assert report.sram_total < sz.SRAM_LIMIT_BYTES


def test_cross_rate_signals_land_in_shared_bank(descriptor, routing) -> None:
    """Cross-rate connections become SRAM2 seqlock buses (RTE-004/BLD-002)."""
    baseline = sz.estimate_footprint(descriptor, {"connections": []})
    routed = sz.estimate_footprint(descriptor, routing)
    assert routed.banks["SRAM2"] > baseline.banks["SRAM2"]


def test_oversized_workspace_is_halted(descriptor) -> None:
    """BLD-004: the estimate must stop a workspace that cannot fit."""
    models = {}
    for i in range(120):
        models[f"Big{i:03d}"] = {
            "file": f"Big{i:03d}.slx",
            "slx_sha256": "d" * 64,
            "base_rate_s": 0.01,
            "rate_group": "slow_10ms",
            "inports": [{"name": f"u{j}", "data_type": "int32", "width": 32}
                        for j in range(8)],
            "outports": [{"name": f"y{j}", "data_type": "int32", "width": 32}
                         for j in range(8)],
            "internal_types": ["int32"],
        }
    report = sz.estimate_footprint({"schema_version": 1, "models": models},
                                   {"connections": []})
    assert not report.ok
    assert any("BLD-004" in r or "BLD-002" in r for r in report.blocking_reasons())
    assert "halted" in report.format_text().lower()


def test_report_text_is_human_readable(descriptor, routing) -> None:
    text = sz.estimate_footprint(descriptor, routing).format_text()
    for expected in ("SRAM0", "SRAM2", "Flash", "verdict", "PASS"):
        assert expected in text


_REAL_MAPS = [
    Path(__file__).parent.parent / "build-gen" / "picodesk_gen_firmware.elf.map",
]


@pytest.mark.skipif(not any(p.is_file() for p in _REAL_MAPS),
                    reason="no generated-firmware map built yet")
def test_estimate_matches_real_linker_map(descriptor, routing) -> None:
    """BLD-004 exit criterion: the pre-build estimate must land within ±10 %
    of what the linker actually produces for the same workspace."""
    map_path = next(p for p in _REAL_MAPS if p.is_file())
    report = sz.estimate_footprint(descriptor, routing)
    comparison = sz.compare_with_map(report, map_path)
    assert comparison["within_tolerance"], (
        f"estimate off by {comparison['relative_error'] * 100:.1f}%: "
        f"{comparison['estimated']} vs {comparison['actual']}")
    # Per-bank accuracy matters too — a right total from wrong banks would
    # not catch a bank overflow (BLD-002).
    for bank, actual in comparison["actual"].items():
        if actual == 0:
            continue
        error = abs(comparison["estimated"][bank] - actual) / actual
        assert error <= 0.15, f"{bank} estimate off by {error * 100:.0f}%"


def test_map_parsing_and_tolerance(tmp_path) -> None:
    """parse_map_usage attributes sections to the right banks."""
    map_file = tmp_path / "fw.elf.map"
    map_file.write_text(
        ".core0_bss      0x0000000021000000       0x400\n"
        ".core1_bss      0x0000000021010000      0x2000\n"
        ".rte_shared     0x0000000021020000      0x1000\n"
        ".data           0x0000000021030000       0x800\n"
        ".text           0x0000000010000000      0x9000\n", encoding="utf-8")
    banks = sz.parse_map_usage(map_file)
    assert banks == {"SRAM0": 0x400, "SRAM1": 0x2000,
                     "SRAM2": 0x1000, "SRAM3": 0x800}

    report = sz.SizingReport(sram_total=sum(banks.values()), banks=banks)
    comparison = sz.compare_with_map(report, map_file)
    assert comparison["relative_error"] == 0.0
    assert comparison["within_tolerance"]


# --- diagnostic parsing (GUI-005 input) -------------------------------------

@pytest.mark.parametrize(("line", "severity", "file", "lineno"), [
    ("SRC/rte/rte_gen.c:214:9: error: 'x' undeclared here",
     "error", "SRC/rte/rte_gen.c", 214),
    ("SRC/src/main.c:88:1: warning: unused variable 'y'",
     "warning", "SRC/src/main.c", 88),
    ("/abs/path/model.c:12: undefined reference to `pd_Foo_step'",
     "error", "/abs/path/model.c", None),
])
def test_diagnostics_are_parsed_for_hyperlinking(line, severity, file,
                                                 lineno) -> None:
    diag = parse_diagnostic(line)
    assert diag is not None
    assert diag.severity == severity
    assert diag.file == file
    if lineno is not None:
        assert diag.line == lineno
    assert diag.as_console_line().startswith(f"[{severity}]")


def test_cmake_errors_are_parsed() -> None:
    diag = parse_diagnostic("CMake Error at CMakeLists.txt:42 (add_executable):")
    assert diag is not None
    assert diag.severity == "error"
    assert diag.file == "CMakeLists.txt"
    assert diag.line == 42


def test_plain_output_is_not_a_diagnostic() -> None:
    assert parse_diagnostic("[41/59] Linking CXX executable fw.elf") is None
