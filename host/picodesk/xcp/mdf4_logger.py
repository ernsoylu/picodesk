"""ASAM MDF4 recording of DAQ streams via asammdf (GUI-012).

Frames arrive from the target one per DAQ event, each carrying the same set of
signals sampled coherently by the fast ISR (RTE-005). This buffers them and
writes a single MDF4 file on stop.

Two decisions worth knowing about:

**Buffer, then write once.** asammdf builds a file from whole channel arrays,
and appending to an open MDF per frame is far slower than the DAQ stream. A
long recording is bounded by `max_samples` instead, which stops accepting
frames and says so, rather than growing until the host swaps.

**Timestamps come from the target, not the host.** The target stamps each DAQ
frame from its own 64-bit microsecond timer; host arrival time includes USB
scheduling and would put jitter into the recording that the system does not
have. `append()` therefore takes the target timestamp.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DEFAULT_MAX_SAMPLES = 2_000_000


class RecordingError(Exception):
    """Misuse of the recorder — wrong state, or a frame that does not fit."""


@dataclass(frozen=True)
class SignalSpec:
    """One recorded channel. `dtype` is any NumPy integer/float dtype string;
    the fast loop is integer-only (MAT-002), but slow-rate models may be float
    and DAQ carries whatever they produce."""

    name: str
    dtype: str = "i4"
    unit: str = ""
    comment: str = ""


class Mdf4Logger:
    """Collect DAQ frames and write them as ASAM MDF4.

    Usage:
        log = Mdf4Logger()
        log.start([SignalSpec("torque_cmd"), SignalSpec("speed_est")])
        log.append(t_us, (torque, speed))
        path = log.stop(Path("run.mf4"))
    """

    def __init__(self, max_samples: int = DEFAULT_MAX_SAMPLES) -> None:
        self.max_samples = max_samples
        self._specs: tuple[SignalSpec, ...] = ()
        self._times: list[float] = []
        self._columns: list[list[float]] = []
        self._recording = False
        self._dropped = 0

    # --- state ------------------------------------------------------------

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def sample_count(self) -> int:
        return len(self._times)

    @property
    def dropped(self) -> int:
        """Frames refused after `max_samples`. Non-zero means the recording is
        truncated, and the caller should surface that rather than present a
        short file as a complete one."""
        return self._dropped

    # --- recording --------------------------------------------------------

    def start(self, specs: Sequence[SignalSpec]) -> None:
        if self._recording:
            raise RecordingError("already recording")
        if not specs:
            raise RecordingError("a recording needs at least one signal")
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise RecordingError(f"duplicate signal names: {names}")
        self._specs = tuple(specs)
        self._times = []
        self._columns = [[] for _ in specs]
        self._dropped = 0
        self._recording = True

    def append(self, timestamp_us: int, values: Sequence[float]) -> bool:
        """Add one coherent DAQ frame. Returns False once the cap is hit."""
        if not self._recording:
            raise RecordingError("append() before start()")
        if len(values) != len(self._specs):
            raise RecordingError(
                f"frame has {len(values)} values, expected {len(self._specs)}")
        if len(self._times) >= self.max_samples:
            self._dropped += 1
            return False
        self._times.append(timestamp_us / 1e6)  # MDF4 timestamps are seconds
        for column, value in zip(self._columns, values):
            column.append(value)
        return True

    def stop(self, out_path: Path) -> Path:
        """Write the buffered frames and reset. Returns the written path."""
        if not self._recording:
            raise RecordingError("stop() before start()")
        if not self._times:
            self._recording = False
            raise RecordingError("nothing was recorded")

        from asammdf import MDF, Signal  # imported late: asammdf is heavy

        times = np.asarray(self._times, dtype="f8")
        signals = [
            Signal(samples=np.asarray(column, dtype=spec.dtype),
                   timestamps=times,
                   name=spec.name,
                   unit=spec.unit,
                   comment=spec.comment)
            for spec, column in zip(self._specs, self._columns)
        ]

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with MDF(version="4.10") as mdf:
            mdf.append(signals, comment="PicoDesk XCP DAQ recording (GUI-012)")
            mdf.save(out_path, overwrite=True)

        self._recording = False
        return out_path
