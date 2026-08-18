"""picodesk.hil.analyze against synthetic captures.

The point of these tests is that the campaign analysis is proven before a board
exists. They are written the way the problem inventory says to write them: each
one that asserts a PASS has a sibling that must FAIL, because an analyser that
cannot report a violation is not measuring anything.
"""

from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path

import pytest
from picodesk.hil import analyze as hil


def write_capture(path: Path, samples: list[tuple[float, int]],
                  channel: str = "D2") -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Time [s]", channel])
        writer.writerows(samples)
    return path


def square_wave(period_us: float, count: int, high_us: float,
                offsets_us: list[float] | None = None) -> list[tuple[float, int]]:
    """Transition-only capture: one rising and one falling row per cycle."""
    rows: list[tuple[float, int]] = [(0.0, 0)]
    for i in range(count):
        offset = offsets_us[i] if offsets_us else 0.0
        rise = (i * period_us + offset) / 1e6
        rows.append((rise, 1))
        rows.append((rise + high_us / 1e6, 0))
    return rows


# --- jitter (NFR-1) -------------------------------------------------------


def test_perfect_grid_reports_no_jitter(tmp_path: Path) -> None:
    cap = write_capture(tmp_path / "clean.csv", square_wave(1000.0, 5000, 200.0))
    capture = hil.load_capture(cap, "D2")
    errors, period = hil.dispatch_jitter_us(hil.edges(capture, rising=True),
                                            1000.0, fit_period=True)
    assert period == pytest.approx(1000.0, abs=1e-6)
    assert max(errors) < 1e-3


def test_injected_jitter_is_recovered(tmp_path: Path) -> None:
    """A known offset on one cycle must show up as that offset, not half of it.

    The fitted grid absorbs a little of any single outlier, so this also pins
    down that the fit does not swallow the thing being measured.
    """
    offsets = [0.0] * 5000
    offsets[2500] = 37.0
    cap = write_capture(tmp_path / "spike.csv",
                        square_wave(1000.0, 5000, 200.0, offsets))
    capture = hil.load_capture(cap, "D2")
    errors, _ = hil.dispatch_jitter_us(hil.edges(capture, rising=True),
                                       1000.0, fit_period=True)
    assert max(errors) == pytest.approx(37.0, abs=0.1)


def test_timebase_offset_is_not_reported_as_jitter(tmp_path: Path) -> None:
    """A 50 ppm clock difference is 50 us of accumulated phase over 1e6 us.

    Measured against the nominal grid that reads as a gross NFR-1 violation;
    fitting the period is what makes the number mean 'jitter'.
    """
    drifted = 1000.05  # 50 ppm slow
    cap = write_capture(tmp_path / "drift.csv", square_wave(drifted, 5000, 200.0))
    capture = hil.load_capture(cap, "D2")
    rising = hil.edges(capture, rising=True)

    fitted, period = hil.dispatch_jitter_us(rising, 1000.0, fit_period=True)
    assert period == pytest.approx(drifted, abs=1e-3)
    assert max(fitted) < 1e-3

    nominal, _ = hil.dispatch_jitter_us(rising, 1000.0, fit_period=False)
    assert max(nominal) > 200.0


def test_verdict_fails_when_the_budget_is_exceeded(tmp_path: Path) -> None:
    rng = random.Random(7)
    # 0.1 % of cycles land 80 us late: p99.99 must land above the 50 us budget.
    offsets = [80.0 if rng.random() < 0.001 else rng.uniform(-2, 2)
               for _ in range(20000)]
    cap = write_capture(tmp_path / "bad.csv",
                        square_wave(1000.0, 20000, 200.0, offsets))
    verdict, _ = hil.analyse_jitter(hil.load_capture(cap, "D2"),
                                    min_samples=10000)
    assert verdict.enough_samples
    assert not verdict.within_budget
    assert not verdict.passed


def test_verdict_passes_within_budget(tmp_path: Path) -> None:
    rng = random.Random(11)
    offsets = [rng.uniform(-5, 5) for _ in range(20000)]
    cap = write_capture(tmp_path / "good.csv",
                        square_wave(1000.0, 20000, 200.0, offsets))
    verdict, _ = hil.analyse_jitter(hil.load_capture(cap, "D2"),
                                    min_samples=10000)
    assert verdict.passed


def test_too_few_samples_is_inconclusive_not_a_pass(tmp_path: Path) -> None:
    """The requirement is p99.99 over 1e6 cycles. A clean 1000-cycle capture
    must not be allowed to masquerade as having met it."""
    cap = write_capture(tmp_path / "short.csv", square_wave(1000.0, 1000, 200.0))
    capture = hil.load_capture(cap, "D2")
    verdict, extra = hil.analyse_jitter(capture)  # default 1e6 floor
    assert verdict.within_budget          # the capture itself is clean...
    assert not verdict.enough_samples     # ...but it cannot support the claim
    assert not verdict.passed
    assert "INCONCLUSIVE" in hil.render(verdict, capture.channel, extra)


# --- pulse width (NFR-3) --------------------------------------------------


def test_pulse_widths_measured(tmp_path: Path) -> None:
    cap = write_capture(tmp_path / "pulse.csv",
                        square_wave(1000.0, 2000, 9.0), channel="D5")
    capture = hil.load_capture(cap, "D5")
    widths = hil.pulse_widths_us(capture)
    assert len(widths) == 2000
    assert max(widths) == pytest.approx(9.0, abs=1e-3)


def test_long_hold_fails_nfr3(tmp_path: Path) -> None:
    rows = square_wave(1000.0, 2000, 9.0)
    # Stretch one cycle's high time to 22 us — over the 15 us budget. Row 0 is
    # the initial low, so cycle i rises at 1 + 2i and falls at 2 + 2i.
    rise_index = 1 + 2 * 900
    rows[rise_index + 1] = (rows[rise_index][0] + 22e-6, 0)
    cap = write_capture(tmp_path / "hold.csv", rows, channel="D5")
    verdict, _ = hil.analyse_pulse(hil.load_capture(cap, "D5"), min_samples=100)
    assert verdict.stats.maximum == pytest.approx(22.0, abs=0.1)
    assert not verdict.passed


def test_short_holds_pass_nfr3(tmp_path: Path) -> None:
    cap = write_capture(tmp_path / "ok.csv", square_wave(1000.0, 2000, 9.0),
                        channel="D5")
    verdict, _ = hil.analyse_pulse(hil.load_capture(cap, "D5"), min_samples=100)
    assert verdict.passed


# --- input handling -------------------------------------------------------


def test_percentile_is_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    assert hil.percentile(values, 0.50) == 50.0
    assert hil.percentile(values, 0.99) == 99.0
    assert hil.percentile(values, 0.9999) == 100.0


def test_channel_resolves_by_name_and_index(tmp_path: Path) -> None:
    cap = write_capture(tmp_path / "c.csv", square_wave(1000.0, 10, 200.0))
    assert hil.load_capture(cap, "D2").channel == "D2"
    assert hil.load_capture(cap, "1").channel == "D2"
    with pytest.raises(hil.CaptureError, match="not in columns"):
        hil.load_capture(cap, "D9")


def test_headerless_capture_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "raw.csv"
    path.write_text("0.0,0\n0.001,1\n", encoding="utf-8")
    with pytest.raises(hil.CaptureError, match="no header row"):
        hil.load_capture(path, "0")


def test_report_records_its_inputs(tmp_path: Path) -> None:
    """The archived artefact has to say which capture and channel produced the
    number, or a folder of results months later is unattributable."""
    cap = write_capture(tmp_path / "j.csv", square_wave(1000.0, 5000, 200.0))
    capture = hil.load_capture(cap, "D2")
    verdict, extra = hil.analyse_jitter(capture, min_samples=1000)
    report = hil.report_dict(verdict, capture, cap, extra)
    assert json.loads(json.dumps(report)) == report  # archivable as-is
    assert report["passed"] is True
    assert report["count"] == 5000
    assert report["channel"] == "D2"
    assert report["capture"] == str(cap)
    assert report["metric"].startswith("NFR-1")
    assert math.isclose(report["period us"], 1000.0, abs_tol=1e-3)
