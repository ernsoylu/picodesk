"""ASAM MDF4 recording of DAQ streams via asammdf (GUI-012)."""

from __future__ import annotations

from pathlib import Path


class Mdf4Logger:
    def start(self, out_path: Path) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError
