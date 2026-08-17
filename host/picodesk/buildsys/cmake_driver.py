"""Drive CMake configure/build for target/ and stream output to the GUI console.

Incremental relinking after a routing-only change must complete within the
NFR-2 budget (45 s). Reproducibility (BLD-008) is enforced in target CMake
flags; this driver verifies it by hashing the resulting .uf2 (SHA-256).
"""

from __future__ import annotations

from pathlib import Path


def configure_and_build(target_dir: Path, build_dir: Path) -> Path:
    """Run CMake + build; return the path to the produced .uf2."""
    raise NotImplementedError


def uf2_hash(uf2_path: Path) -> str:
    raise NotImplementedError
