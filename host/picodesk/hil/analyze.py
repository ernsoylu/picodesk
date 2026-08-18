"""Analyse a logic-analyzer capture against the PicoDesk timing budgets.

Two gates need a scope rather than an emulator, and both read the same kind of
capture — a digital channel toggled by the firmware:

  NFR-1 (O-1)  dispatch jitter of the core 0 fast ISR, <= 50 us at p99.99 over
               1,000,000 cycles. GPIO 2 goes high on ISR entry, low on exit
               (target/rte/rte_dispatch.c), so the rising edges are the
               dispatch instants.

  NFR-3 (O-2)  no critical section holds off interrupts for longer than 15 us.
               Probe whichever line the section under test toggles and measure
               high time.

Input is CSV with a float time column in seconds and one or more 0/1 digital
columns; that covers Saleae Logic 2 and sigrok/PulseView exports.

Why the sample-count floor exists: a p99.99 estimated from a few thousand
edges is noise wearing a percentile's name. Callers get `enough_samples` as a
separate signal from `within_budget`, so a short clean capture reports
INCONCLUSIVE rather than a comfortable PASS.
"""

from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path

NFR1_BUDGET_US = 50.0
NFR1_CYCLES = 1_000_000
NFR3_BUDGET_US = 15.0


class CaptureError(Exception):
    """The capture cannot be interpreted — bad columns, no edges, no time."""


@dataclass
class Capture:
    times_s: list[float]
    levels: list[int]
    channel: str


@dataclass
class Stats:
    count: int
    p50: float
    p99: float
    p999: float
    p9999: float
    maximum: float
    mean: float

    def as_dict(self) -> dict[str, float | int]:
        return {"count": self.count, "p50": self.p50, "p99": self.p99,
                "p99.9": self.p999, "p99.99": self.p9999,
                "max": self.maximum, "mean": self.mean}


@dataclass
class Verdict:
    metric: str
    budget_us: float
    stats: Stats
    min_samples: int
    notes: list[str] = field(default_factory=list)

    @property
    def enough_samples(self) -> bool:
        return self.stats.count >= self.min_samples

    @property
    def within_budget(self) -> bool:
        return self.stats.p9999 <= self.budget_us

    @property
    def passed(self) -> bool:
        return self.enough_samples and self.within_budget


def percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile. q in [0, 1].

    Nearest-rank, not interpolated: an interpolated p99.99 invents a value
    between two real samples, and for a worst-case budget the honest answer is
    an observed one.
    """
    if not sorted_values:
        raise CaptureError("no samples")
    rank = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def summarise(values: list[float]) -> Stats:
    ordered = sorted(values)
    return Stats(count=len(ordered),
                 p50=percentile(ordered, 0.50),
                 p99=percentile(ordered, 0.99),
                 p999=percentile(ordered, 0.999),
                 p9999=percentile(ordered, 0.9999),
                 maximum=ordered[-1],
                 mean=statistics.fmean(ordered))


def _resolve_column(header: list[str], channel: str) -> int:
    stripped = [h.strip() for h in header]
    if channel in stripped:
        return stripped.index(channel)
    lowered = [h.lower() for h in stripped]
    if channel.lower() in lowered:
        return lowered.index(channel.lower())
    if channel.isdigit() and int(channel) < len(stripped):
        return int(channel)
    raise CaptureError(f"channel {channel!r} not in columns {stripped}")


def load_capture(path: Path, channel: str) -> Capture:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise CaptureError(f"{path} is empty")

    header = rows[0]
    try:
        float(header[0])
    except (ValueError, IndexError):
        body = rows[1:]
    else:
        raise CaptureError(
            f"{path} has no header row; name the time and channel columns so "
            "the channel being analysed is recorded in the artefact")

    column = _resolve_column(header, channel)
    times: list[float] = []
    levels: list[int] = []
    for line, row in enumerate(body, start=2):
        if not row or len(row) <= column:
            continue
        try:
            times.append(float(row[0]))
            levels.append(1 if float(row[column]) >= 0.5 else 0)
        except ValueError as exc:
            raise CaptureError(f"{path}:{line}: {exc}") from exc

    if len(times) < 2:
        raise CaptureError(f"{path}: fewer than two samples")
    return Capture(times_s=times, levels=levels, channel=header[column].strip())


def edges(capture: Capture, rising: bool) -> list[float]:
    """Times (seconds) of level transitions.

    Works for both sample-per-row captures and transition-only exports: a row
    whose level equals the previous row's is simply not an edge.
    """
    want = (0, 1) if rising else (1, 0)
    out: list[float] = []
    for i in range(1, len(capture.levels)):
        if (capture.levels[i - 1], capture.levels[i]) == want:
            out.append(capture.times_s[i])
    return out


def dispatch_jitter_us(edge_times_s: list[float], nominal_period_us: float,
                       fit_period: bool) -> tuple[list[float], float]:
    """Phase error of each dispatch against the ideal periodic grid.

    Returns (absolute errors in us, period used in us).

    The period is fitted by least squares by default rather than assumed: the
    RP2040 crystal and the analyzer's timebase are independent, and an
    uncorrected offset of even 10 ppm accumulates 10 us over a 1 s capture —
    which would be reported as jitter the system does not have. Pass
    --no-fit-period to measure against the nominal grid instead, which is what
    you want if you are hunting absolute drift rather than jitter.
    """
    if len(edge_times_s) < 2:
        raise CaptureError("need at least two edges to measure jitter")

    n = len(edge_times_s)
    indices = list(range(n))
    if fit_period:
        # Least-squares fit of t = t0 + period * i.
        mean_i = statistics.fmean(indices)
        mean_t = statistics.fmean(edge_times_s)
        num = sum((i - mean_i) * (t - mean_t) for i, t in zip(indices, edge_times_s))
        den = sum((i - mean_i) ** 2 for i in indices)
        if den == 0:
            raise CaptureError("degenerate edge series")
        period_s = num / den
        t0 = mean_t - period_s * mean_i
    else:
        period_s = nominal_period_us / 1e6
        t0 = edge_times_s[0]

    errors = [abs((t - (t0 + period_s * i)) * 1e6)
              for i, t in zip(indices, edge_times_s)]
    return errors, period_s * 1e6


def pulse_widths_us(capture: Capture) -> list[float]:
    """High-time of each complete pulse, in microseconds."""
    widths: list[float] = []
    start: float | None = None
    for i in range(1, len(capture.levels)):
        prev, now = capture.levels[i - 1], capture.levels[i]
        if (prev, now) == (0, 1):
            start = capture.times_s[i]
        elif (prev, now) == (1, 0) and start is not None:
            widths.append((capture.times_s[i] - start) * 1e6)
            start = None
    if not widths:
        raise CaptureError("no complete high pulses found on this channel")
    return widths


def render(verdict: Verdict, channel: str, extra: dict[str, float]) -> str:
    s = verdict.stats
    lines = [
        f"{verdict.metric} on channel {channel}",
        f"  samples      {s.count:,}  (need {verdict.min_samples:,})",
        f"  mean         {s.mean:9.3f} us",
        f"  p50          {s.p50:9.3f} us",
        f"  p99          {s.p99:9.3f} us",
        f"  p99.9        {s.p999:9.3f} us",
        f"  p99.99       {s.p9999:9.3f} us   budget {verdict.budget_us:.1f} us",
        f"  max          {s.maximum:9.3f} us",
    ]
    for key, value in extra.items():
        lines.append(f"  {key:<12} {value:9.3f}")
    for note in verdict.notes:
        lines.append(f"  note: {note}")

    if not verdict.enough_samples:
        lines.append(
            f"  INCONCLUSIVE: {s.count:,} samples cannot support a p99.99 "
            f"claim; capture at least {verdict.min_samples:,}")
    elif verdict.within_budget:
        lines.append("  PASS")
    else:
        lines.append(
            f"  FAIL: p99.99 {s.p9999:.3f} us exceeds the {verdict.budget_us:.1f} us budget")
    return "\n".join(lines)


def analyse_jitter(capture: Capture, period_us: float = 1000.0,
                   budget_us: float = NFR1_BUDGET_US,
                   min_samples: int = NFR1_CYCLES,
                   fit_period: bool = True) -> tuple[Verdict, dict[str, float]]:
    errors, fitted_us = dispatch_jitter_us(edges(capture, rising=True),
                                           period_us, fit_period)
    notes: list[str] = []
    drift_ppm = (fitted_us - period_us) / period_us * 1e6
    if fit_period and abs(drift_ppm) > 100:
        notes.append(
            f"fitted period differs from nominal by {drift_ppm:.0f} ppm — "
            "check the analyzer timebase before trusting this")
    verdict = Verdict(metric="NFR-1 dispatch jitter", budget_us=budget_us,
                      stats=summarise(errors), min_samples=min_samples,
                      notes=notes)
    return verdict, {"period us": fitted_us}


def analyse_pulse(capture: Capture, budget_us: float = NFR3_BUDGET_US,
                  min_samples: int = 1000) -> tuple[Verdict, dict[str, float]]:
    verdict = Verdict(metric="NFR-3 critical-section hold", budget_us=budget_us,
                      stats=summarise(pulse_widths_us(capture)),
                      min_samples=min_samples)
    return verdict, {}


def report_dict(verdict: Verdict, capture: Capture, source: Path,
                extra: dict[str, float]) -> dict[str, object]:
    """The archived artefact for a campaign run. Records the inputs alongside
    the numbers, so a result in a release folder can be traced back to which
    channel and which capture produced it."""
    return {
        "metric": verdict.metric,
        "channel": capture.channel,
        "capture": str(source),
        "budget_us": verdict.budget_us,
        "min_samples": verdict.min_samples,
        "enough_samples": verdict.enough_samples,
        "passed": verdict.passed,
        "notes": verdict.notes,
        **verdict.stats.as_dict(),
        **extra,
    }


