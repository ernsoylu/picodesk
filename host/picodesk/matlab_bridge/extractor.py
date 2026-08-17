"""Batch .slx extraction to the monolithic model descriptor JSON (MAT-001).

Each .slx is content-hashed; a changed hash forces re-export of that model's
descriptor and marks dependent I/O stale (GUI-001). The same hash gates ERT
re-generation for incremental builds (NFR-2). Fast-loop models whose compiled
metrics contain single/double types must fail the build hard (MAT-002).
"""

from __future__ import annotations

from pathlib import Path

from picodesk.matlab_bridge.session import MatlabSession


def extract_models(session: MatlabSession, slx_dir: Path) -> dict:
    """Return the monolithic descriptor: per-model I/O, data types, base rates."""
    raise NotImplementedError
