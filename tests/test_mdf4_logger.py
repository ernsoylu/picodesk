"""MDF4 recording of DAQ streams (GUI-012).

These write real MDF4 files with asammdf and read them back, so the format
claim is checked rather than asserted. No hardware is involved: the frames are
synthetic, which is exactly what makes this testable — the part that needs a
board is the DAQ stream arriving, not the file being written correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from asammdf import MDF
from picodesk.xcp.mdf4_logger import Mdf4Logger, RecordingError, SignalSpec

SPECS = [
    SignalSpec("torque_cmd", "i4", unit="Nm"),
    SignalSpec("speed_est", "i4", unit="rpm"),
]


def record(tmp_path: Path, frames: list[tuple[int, tuple[int, int]]],
           specs: list[SignalSpec] | None = None) -> Path:
    log = Mdf4Logger()
    log.start(specs or SPECS)
    for timestamp, values in frames:
        log.append(timestamp, values)
    return log.stop(tmp_path / "run.mf4")


def test_round_trip_preserves_signals_and_timebase(tmp_path: Path) -> None:
    frames = [(i * 1000, (-100 - i, 4000 + i)) for i in range(500)]
    out = record(tmp_path, frames)

    with MDF(out) as mdf:
        names = {ch.name for group in mdf.groups for ch in group.channels}
        assert {"torque_cmd", "speed_est"} <= names

        torque = mdf.get("torque_cmd")
        speed = mdf.get("speed_est")
        assert list(torque.samples[:3]) == [-100, -101, -102]
        assert list(speed.samples[:3]) == [4000, 4001, 4002]
        assert torque.unit == "Nm"

        # Target timestamps are microseconds; MDF4 stores seconds.
        assert torque.timestamps[0] == pytest.approx(0.0)
        assert torque.timestamps[1] == pytest.approx(0.001)
        assert torque.timestamps[-1] == pytest.approx(0.499)
        assert all(b > a for a, b in zip(torque.timestamps, torque.timestamps[1:]))


def test_signed_values_survive_the_round_trip(tmp_path: Path) -> None:
    """int32 negatives are where a wrong dtype shows up first."""
    frames = [(0, (-2147483648, 2147483647)), (1000, (-1, 0))]
    out = record(tmp_path, frames)
    with MDF(out) as mdf:
        assert list(mdf.get("torque_cmd").samples) == [-2147483648, -1]
        assert list(mdf.get("speed_est").samples) == [2147483647, 0]


def test_float_signals_are_supported(tmp_path: Path) -> None:
    """The fast loop is integer-only (MAT-002), but slow-rate models are not,
    and DAQ carries whatever they produce."""
    specs = [SignalSpec("temp_c", "f4"), SignalSpec("derate", "f8")]
    out = record(tmp_path, [(0, (21.5, 0.25)), (1000, (22.0, 0.5))], specs)
    with MDF(out) as mdf:
        assert list(mdf.get("temp_c").samples) == pytest.approx([21.5, 22.0])
        assert list(mdf.get("derate").samples) == pytest.approx([0.25, 0.5])


# --- state and misuse -----------------------------------------------------


def test_sample_count_tracks_appends() -> None:
    log = Mdf4Logger()
    assert not log.recording
    log.start(SPECS)
    assert log.recording and log.sample_count == 0
    log.append(0, (1, 2))
    log.append(1000, (3, 4))
    assert log.sample_count == 2


def test_cap_truncates_and_says_so() -> None:
    """A truncated recording must be visible as truncated. Silently returning
    a short file would present partial data as a complete run."""
    log = Mdf4Logger(max_samples=3)
    log.start(SPECS)
    accepted = [log.append(i * 1000, (i, i)) for i in range(5)]
    assert accepted == [True, True, True, False, False]
    assert log.sample_count == 3
    assert log.dropped == 2


def test_wrong_frame_width_is_rejected() -> None:
    log = Mdf4Logger()
    log.start(SPECS)
    with pytest.raises(RecordingError, match="expected 2"):
        log.append(0, (1, 2, 3))


def test_append_before_start_is_rejected() -> None:
    with pytest.raises(RecordingError, match="before start"):
        Mdf4Logger().append(0, (1, 2))


def test_duplicate_signal_names_are_rejected() -> None:
    log = Mdf4Logger()
    with pytest.raises(RecordingError, match="duplicate"):
        log.start([SignalSpec("x"), SignalSpec("x")])


def test_empty_recording_does_not_write_a_file(tmp_path: Path) -> None:
    """An empty MDF4 would look like a successful capture of nothing."""
    log = Mdf4Logger()
    log.start(SPECS)
    with pytest.raises(RecordingError, match="nothing was recorded"):
        log.stop(tmp_path / "empty.mf4")
    assert not (tmp_path / "empty.mf4").exists()
    assert not log.recording


def test_restart_after_stop_clears_the_buffer(tmp_path: Path) -> None:
    log = Mdf4Logger()
    log.start(SPECS)
    log.append(0, (1, 2))
    log.stop(tmp_path / "first.mf4")

    log.start(SPECS)
    assert log.sample_count == 0
    log.append(0, (9, 9))
    out = log.stop(tmp_path / "second.mf4")
    with MDF(out) as mdf:
        assert list(mdf.get("torque_cmd").samples) == [9]
