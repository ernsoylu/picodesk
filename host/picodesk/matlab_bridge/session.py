"""Persistent MATLAB engine session with crash recovery (GUI-002).

matlabengine is imported lazily and is not a declared dependency: its version
is locked to the locally installed MATLAB release (R2023b-R2024b).
"""

from __future__ import annotations


class MatlabSession:
    """Owns a single long-lived matlab.engine instance and restarts it on crash."""

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def eval(self, command: str) -> object:
        """Forward a command string to matlab.engine's eval — this mirrors the
        MATLAB Engine API and never touches Python's builtin eval()."""
        raise NotImplementedError
