#!/usr/bin/env python3
"""Print the static sizing report for a workspace and gate the build (BLD-004).

Exits non-zero when the estimate exceeds the SRAM/Flash ceilings, so CI and
the GUI use the same gate. With --map, also compares the estimate against a
real linker map (the ±10 % calibration check).

Usage:
  tools/sizing_report.py --descriptor D.json --routing R.json [--map FW.elf.map]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "host"))

from picodesk.buildsys import sizing_report as sz


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--routing", type=Path)
    parser.add_argument("--map", type=Path, dest="map_path")
    args = parser.parse_args(argv)

    descriptor = json.loads(args.descriptor.read_text(encoding="utf-8"))
    routing = (json.loads(args.routing.read_text(encoding="utf-8"))
               if args.routing else None)

    report = sz.estimate_footprint(descriptor, routing)
    print(report.format_text())

    if args.map_path is not None:
        comparison = sz.compare_with_map(report, args.map_path)
        print("\nCalibration against the linker map:")
        for bank in sorted(comparison["actual"]):
            print(f"  {bank:<8} est {comparison['estimated'][bank] / 1024:7.1f} kB"
                  f"   actual {comparison['actual'][bank] / 1024:7.1f} kB")
        print(f"  total error {comparison['relative_error'] * 100:+.1f}% "
              f"(within +/-10%: {comparison['within_tolerance']})")
        if not comparison["within_tolerance"]:
            return 2

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
