#!/usr/bin/env python3
"""Analyse a logic-analyzer capture against the NFR-1 / NFR-3 budgets.

The two gates that need a scope rather than an emulator (O-1, O-2) read the
same kind of capture: a digital channel the firmware toggles. GPIO 2 goes high
on fast-ISR entry and low on exit (target/rte/rte_dispatch.c), so its rising
edges are the dispatch instants and its high time is the ISR execution time.

  hil_analyze.py jitter capture.csv --channel D2 --period-us 1000
  hil_analyze.py pulse  capture.csv --channel D5 --budget-us 15

Exit status: 0 pass, 1 fail or inconclusive, 2 unreadable capture. See
docs/HARDWARE_CAMPAIGN.md for the wiring and capture settings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "host"))

from picodesk.hil import analyze as an


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("capture", type=Path)
    common.add_argument("--channel", required=True,
                        help="column name or index of the digital channel")
    common.add_argument("--json", type=Path, help="write the result as JSON")

    j = sub.add_parser("jitter", parents=[common],
                       help="NFR-1 dispatch jitter from rising edges")
    j.add_argument("--period-us", type=float, default=1000.0)
    j.add_argument("--budget-us", type=float, default=an.NFR1_BUDGET_US)
    j.add_argument("--min-samples", type=int, default=an.NFR1_CYCLES)
    j.add_argument("--no-fit-period", dest="fit_period", action="store_false",
                   help="measure against the nominal grid instead of a fitted one")

    p = sub.add_parser("pulse", parents=[common],
                       help="NFR-3 hold time from high-pulse widths")
    p.add_argument("--budget-us", type=float, default=an.NFR3_BUDGET_US)
    p.add_argument("--min-samples", type=int, default=1000)

    args = parser.parse_args(argv)

    try:
        capture = an.load_capture(args.capture, args.channel)
        if args.mode == "jitter":
            verdict, extra = an.analyse_jitter(
                capture, period_us=args.period_us, budget_us=args.budget_us,
                min_samples=args.min_samples, fit_period=args.fit_period)
        else:
            verdict, extra = an.analyse_pulse(
                capture, budget_us=args.budget_us, min_samples=args.min_samples)
    except an.CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(an.render(verdict, capture.channel, extra))

    if args.json:
        args.json.write_text(
            json.dumps(an.report_dict(verdict, capture, args.capture, extra),
                       indent=2),
            encoding="utf-8")
        print(f"wrote {args.json}")

    return 0 if verdict.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
