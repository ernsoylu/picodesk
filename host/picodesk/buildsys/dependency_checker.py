"""Verify the build toolchain at startup (GUI-004).

Builds stay disabled in the GUI until every *required* check passes. Version
floors come from the SRS section 8 matrix: ARM GCC 12.2.rel1 (exact minor pin),
CMake >= 3.20, Pico SDK >= 1.5.1. MATLAB is reported but not required — it is
only needed for model extraction, not for firmware builds.

Also usable standalone:  python -m picodesk.buildsys.dependency_checker [--strict] [--json]
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ARM_GCC_PIN = (12, 2)
CMAKE_MIN = (3, 20)
PICO_SDK_MIN = (1, 5, 1)
PYTHON_RANGE = ((3, 9), (3, 12))

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SDK = _REPO_ROOT / "external" / "pico-sdk"


@dataclass
class DependencyStatus:
    name: str
    required: bool
    found: bool
    version: str
    ok: bool
    detail: str = ""


def _parse_version(text: str) -> tuple | None:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def _tool_version(exe: str, args: tuple = ("--version",)) -> str | None:
    path = shutil.which(exe)
    if path is None:
        return None
    try:
        out = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=15, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.splitlines()[0] if out else ""


def check_arm_gcc() -> DependencyStatus:
    line = _tool_version("arm-none-eabi-gcc")
    if line is None:
        return DependencyStatus(
            "arm-none-eabi-gcc", True, False, "", False, "not on PATH"
        )
    ver = _parse_version(line)
    ok = ver is not None and ver[:2] == ARM_GCC_PIN
    detail = "" if ok else f"pinned to {ARM_GCC_PIN[0]}.{ARM_GCC_PIN[1]}.rel1 (SRS section 8)"
    return DependencyStatus(
        "arm-none-eabi-gcc", True, True, ".".join(map(str, ver or ())), ok, detail
    )


def check_cmake() -> DependencyStatus:
    line = _tool_version("cmake")
    if line is None:
        return DependencyStatus("cmake", True, False, "", False, "not on PATH")
    ver = _parse_version(line)
    ok = ver is not None and ver >= CMAKE_MIN
    return DependencyStatus("cmake", True, True, ".".join(map(str, ver or ())), ok)


def check_pico_sdk() -> DependencyStatus:
    sdk = Path(os.environ.get("PICO_SDK_PATH", _DEFAULT_SDK))
    version_file = sdk / "pico_sdk_version.cmake"
    if not version_file.is_file():
        return DependencyStatus(
            "pico-sdk", True, False, "", False, f"no SDK at {sdk} (set PICO_SDK_PATH)"
        )
    text = version_file.read_text(encoding="utf-8", errors="replace")
    parts = {}
    for key in ("MAJOR", "MINOR", "REVISION"):
        m = re.search(rf"PICO_SDK_VERSION_{key}\s+(\d+)", text)
        parts[key] = int(m.group(1)) if m else 0
    ver = (parts["MAJOR"], parts["MINOR"], parts["REVISION"])
    ok = ver >= PICO_SDK_MIN
    return DependencyStatus("pico-sdk", True, True, ".".join(map(str, ver)), ok, str(sdk))


def check_ninja() -> DependencyStatus:
    line = _tool_version("ninja")
    if line is None:
        return DependencyStatus("ninja", False, False, "", False, "not on PATH (optional)")
    ver = _parse_version(line)
    return DependencyStatus("ninja", False, True, ".".join(map(str, ver or ())), True)


def check_matlab() -> DependencyStatus:
    path = shutil.which("matlab")
    if path is None:
        return DependencyStatus(
            "matlab", False, False, "", False, "not on PATH — model extraction unavailable"
        )
    return DependencyStatus("matlab", False, True, "", True, path)


def check_python() -> DependencyStatus:
    ver = sys.version_info[:3]
    lo, hi = PYTHON_RANGE
    ok = lo <= ver[:2] < hi
    detail = "" if ok else "MATLAB Engine requires Python 3.9-3.11 (SRS section 8)"
    return DependencyStatus("python", True, True, ".".join(map(str, ver)), ok, detail)


def check_dependencies() -> list[DependencyStatus]:
    return [
        check_arm_gcc(),
        check_cmake(),
        check_pico_sdk(),
        check_ninja(),
        check_python(),
        check_matlab(),
    ]


def builds_allowed(statuses: list[DependencyStatus]) -> bool:
    """GUI-004: builds are disabled unless every required dependency is ok.

    Python-version alignment only gates MATLAB extraction, not firmware builds,
    so it is excluded here.
    """
    return all(s.ok for s in statuses if s.required and s.name != "python")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    statuses = check_dependencies()
    if "--json" in args:
        print(json.dumps([asdict(s) for s in statuses], indent=2))
    else:
        for s in statuses:
            mark = "ok " if s.ok else ("MISS" if not s.found else "BAD ")
            req = "required" if s.required else "optional"
            print(f"[{mark}] {s.name:<18} {s.version:<10} {req:<8} {s.detail}")
        print(f"builds allowed: {builds_allowed(statuses)}")
    if "--strict" in args and not builds_allowed(statuses):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
