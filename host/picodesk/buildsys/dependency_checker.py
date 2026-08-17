"""Verify arm-none-eabi-gcc, CMake, and the Pico SDK on PATH at startup (GUI-004).

Builds stay disabled in the GUI until every check passes. Version floors come
from the SRS section 8 matrix (ARM GCC 12.2.rel1, CMake 3.20+, Pico SDK 1.5.1+).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DependencyStatus:
    name: str
    found: bool
    version: str
    ok: bool
    detail: str = ""


def check_dependencies() -> list[DependencyStatus]:
    raise NotImplementedError
