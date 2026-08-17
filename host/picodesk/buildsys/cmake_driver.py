"""CMake orchestration with streamed, parse-friendly output (Phase 6).

Drives configure/build/relink for target/, streaming every line to a callback
so the GUI diagnostic console can render it live (GUI-005). Diagnostics are
regex-parsed into structured records (file, line, severity, message) ready to
hyperlink.

Also owns the two build-level requirements:
  - BLD-008 reproducibility gate: build twice into independent trees and
    compare UF2 hashes.
  - NFR-2 benchmark: time a routing-only rebuild against the 45 s budget.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

LineSink = Callable[[str], None]

NFR2_BUDGET_S = 45.0

#: GCC/Clang diagnostics and CMake errors, for GUI-005 hyperlinking.
_GCC_RE = re.compile(
    r"^(?P<file>[^:\s][^:]*):(?P<line>\d+):(?:(?P<col>\d+):)?\s+"
    r"(?P<severity>error|warning|note):\s+(?P<message>.*)$")
_CMAKE_RE = re.compile(
    r"^CMake (?P<severity>Error|Warning)(?: at (?P<file>[^:]+):(?P<line>\d+))?")
_LD_RE = re.compile(r"^(?P<file>[^:\s][^:]*):\s*(?P<message>.*undefined reference.*)$")


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    message: str
    file: str | None = None
    line: int | None = None

    def as_console_line(self) -> str:
        where = f"{self.file}:{self.line}: " if self.file else ""
        return f"[{self.severity}] {where}{self.message}"


@dataclass
class BuildResult:
    ok: bool
    duration_s: float
    diagnostics: list[Diagnostic]
    uf2: Path | None
    log: list[str]

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]


def parse_diagnostic(line: str) -> Diagnostic | None:
    m = _GCC_RE.match(line)
    if m:
        return Diagnostic(severity=m.group("severity"),
                          message=m.group("message"),
                          file=m.group("file"), line=int(m.group("line")))
    m = _CMAKE_RE.match(line)
    if m:
        return Diagnostic(
            severity=m.group("severity").lower(), message=line.strip(),
            file=m.group("file"),
            line=int(m.group("line")) if m.group("line") else None)
    m = _LD_RE.match(line)
    if m:
        return Diagnostic(severity="error", message=m.group("message"),
                          file=m.group("file"))
    return None


def _run(cmd: list[str], cwd: Path | None, sink: LineSink | None,
         log: list[str], diagnostics: list[Diagnostic]) -> int:
    proc = subprocess.Popen(
        cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        log.append(line)
        if sink is not None:
            sink(line)
        diag = parse_diagnostic(line)
        if diag is not None:
            diagnostics.append(diag)
    return proc.wait()


def configure_and_build(
    target_dir: Path,
    build_dir: Path,
    *,
    sink: LineSink | None = None,
    defines: dict[str, str] | None = None,
    targets: Iterable[str] = (),
    generator: str = "Ninja",
) -> BuildResult:
    """Configure (idempotent) and build; stream output through `sink`."""
    diagnostics: list[Diagnostic] = []
    log: list[str] = []
    started = time.monotonic()

    configure_cmd = ["cmake", "-S", str(target_dir), "-B", str(build_dir),
                     "-G", generator, "-DCMAKE_BUILD_TYPE=Release"]
    for key, value in (defines or {}).items():
        configure_cmd.append(f"-D{key}={value}")

    rc = _run(configure_cmd, None, sink, log, diagnostics)
    if rc != 0:
        return BuildResult(False, time.monotonic() - started, diagnostics,
                           None, log)

    build_cmd = ["cmake", "--build", str(build_dir)]
    for target in targets:
        build_cmd += ["--target", target]
    rc = _run(build_cmd, None, sink, log, diagnostics)

    uf2 = next(iter(sorted(build_dir.glob("*.uf2"))), None)
    return BuildResult(rc == 0 and uf2 is not None,
                       time.monotonic() - started, diagnostics, uf2, log)


def uf2_hash(uf2_path: Path) -> str:
    return hashlib.sha256(uf2_path.read_bytes()).hexdigest()


def verify_reproducible(target_dir: Path, work_dir: Path, *,
                        sink: LineSink | None = None,
                        defines: dict[str, str] | None = None) -> tuple[bool, str, str]:
    """BLD-008 release gate: two independent trees must yield the same UF2."""
    results = []
    for name in ("repro-a", "repro-b"):
        result = configure_and_build(target_dir, work_dir / name,
                                     sink=sink, defines=defines)
        if not result.ok or result.uf2 is None:
            return False, "", ""
        results.append(uf2_hash(result.uf2))
    return results[0] == results[1], results[0], results[1]


def benchmark_routing_change(
    target_dir: Path,
    build_dir: Path,
    regenerate: Callable[[], None],
    *,
    sink: LineSink | None = None,
    defines: dict[str, str] | None = None,
) -> tuple[float, bool]:
    """NFR-2: time a routing-only change — regenerate the RTE, then relink,
    with all unchanged models already built. Returns (seconds, within_budget).
    """
    warm = configure_and_build(target_dir, build_dir, sink=sink,
                               defines=defines)
    if not warm.ok:
        raise RuntimeError("warm-up build failed; cannot benchmark NFR-2")

    started = time.monotonic()
    regenerate()
    result = configure_and_build(target_dir, build_dir, sink=sink,
                                 defines=defines)
    elapsed = time.monotonic() - started
    if not result.ok:
        raise RuntimeError("incremental build failed during NFR-2 benchmark")
    return elapsed, elapsed <= NFR2_BUDGET_S
