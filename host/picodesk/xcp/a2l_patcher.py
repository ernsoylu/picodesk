"""DWARF-aware A2L address patching (CAL-002).

Parses the -g ELF with pyelftools to resolve inner-member VMA addresses of
nested Simulink structs (RECORD_LAYOUT), then rewrites the A2L segments that
ERT generated against the RAM_SHARED layout (MAT-003).
"""

from __future__ import annotations

from pathlib import Path


def patch_a2l(elf_path: Path, a2l_path: Path, out_path: Path) -> None:
    raise NotImplementedError
