#!/usr/bin/env python3
"""Build a generated workspace using real ERT model code (closes O-9).

Replaces the hand-written stand-in step functions with Embedded Coder
output: for every model in the descriptor it runs ERT codegen through the
persistent MATLAB session, emits the RTE adapter, and arranges the sources
so the linker places fast-loop model code in SRAM (BLD-001).

Layout produced under OUTDIR:

    rte_gen.c/.h, pd_<Model>_io.h      the RTE itself
    models/fast/                        ERT sources + adapters, fast group
    models/slow/                        ERT sources + adapters, slower groups
    models/include/                     shared ERT headers (rtwtypes.h, ...)

`*/fast/*` is what the linker script matches to put that code in RAM, so
the directory split is load-bearing, not cosmetic.

Usage:
  tools/ert_integrate.py --descriptor D.json --routing R.json \
      --slx-dir DIR --out OUTDIR
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "host"))

from picodesk.matlab_bridge.descriptor import RATE_GROUP_PERIOD_S, migrate_descriptor
from picodesk.matlab_bridge.extractor import apply_rate_assignments
from picodesk.matlab_bridge.session import MatlabEngineSession
from picodesk.rtegen.generator import (
    generate_ert_adapters,
    generate_rte,
)
from picodesk.rtegen.routing import load_hal_manifest, migrate_routing

#: ERT emits these next to the model; they are shared, not per-model.
SHARED_HEADERS = {"rtwtypes.h", "multiword_types.h", "rtw_continuous.h",
                  "rtw_solver.h", "builtin_typeid_types.h", "zero_crossing_types.h"}
#: Never compiled: sample main and generated data files we do not use.
SKIP_SOURCES = {"ert_main.c", "rt_main.c"}


def codegen_all(session: MatlabEngineSession, descriptor: dict,
                slx_dir: Path, work: Path) -> dict[str, dict]:
    """Run ERT codegen for every model; returns name -> generated info."""
    results: dict[str, dict] = {}
    for name in sorted(descriptor["models"]):
        slx = slx_dir / descriptor["models"][name].get("file", f"{name}.slx")
        if not slx.is_file():
            raise FileNotFoundError(f"{name}: {slx} not found")
        group = descriptor["models"][name]["rate_group"]
        if group is None:
            raise ValueError(
                f"{name}: no rate group — assign one in the routing config "
                f"(rate_assignments) before integration (RTE-002)")
        fast = group == "fast_1ms"
        # The assigned rate is FORCED at codegen (G-2): a rate-agnostic model
        # is generated at exactly the dispatcher slot it was assigned to.
        raw = session.call("picodesk_codegen", str(slx), str(work),
                           float(RATE_GROUP_PERIOD_S[group]))
        info = json.loads(raw)
        gen_dir = Path(info["dir"])
        # tmwinternal/ and slprj/ hold build bookkeeping, not model code.
        info["sources"] = sorted(p.name for p in gen_dir.glob("*.c"))
        info["headers"] = sorted(p.name for p in gen_dir.glob("*.h"))
        results[name] = info
        if fast:
            check_no_float(name, gen_dir, info["sources"])
        mode = "fast: integer-only verified" if fast else "slow: float allowed"
        print(f"  {name}: {len(info['sources'])} source(s), "
              f"{len(info['headers'])} header(s) ({mode})")
    return results


class FloatInGeneratedCodeError(RuntimeError):
    """MAT-002, checked against the artefact rather than a codegen flag."""


def check_no_float(name: str, gen_dir: Path, sources: list[str]) -> None:
    """Fail if ERT emitted floating-point types for a fast-loop model.

    The extractor already rejects float-typed fast models, but that reads
    the model; this reads what the coder actually produced, which is the
    thing that runs in the 1 ms ISR.
    """
    offenders: list[str] = []
    for source in sources:
        text = (gen_dir / source).read_text(encoding="utf-8", errors="replace")
        for token in ("real_T", "real32_T"):
            if re.search(rf"\b{token}\b", text):
                offenders.append(f"{source}: {token}")
    if offenders:
        raise FloatInGeneratedCodeError(
            f"{name} is a fast-loop model but ERT emitted floating-point "
            f"types ({'; '.join(offenders)}) — the RP2040 has no FPU "
            f"(MAT-002)")


def arrange(descriptor: dict, generated: dict[str, dict],
            out_dir: Path) -> dict[str, int]:
    """Copy ERT output into the fast/slow split the linker script expects."""
    models_dir = out_dir / "models"
    include_dir = models_dir / "include"
    for sub in ("fast", "slow", "include"):
        (models_dir / sub).mkdir(parents=True, exist_ok=True)

    counts = {"fast": 0, "slow": 0, "headers": 0}
    for name, info in generated.items():
        fast = descriptor["models"][name]["rate_group"] == "fast_1ms"
        bucket = models_dir / ("fast" if fast else "slow")
        gen_dir = Path(info["dir"])

        for source in info["sources"]:
            if source in SKIP_SOURCES:
                continue
            shutil.copy2(gen_dir / source, bucket / source)
            counts["fast" if fast else "slow"] += 1

        for header in info["headers"]:
            # Shared headers go to the common include dir; per-model headers
            # sit beside their source so both buckets can include them.
            target = include_dir if header in SHARED_HEADERS else bucket
            destination = target / header
            if not destination.exists():
                shutil.copy2(gen_dir / header, destination)
                counts["headers"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--routing", type=Path, required=True)
    parser.add_argument("--slx-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--hal-manifest", type=Path,
                        default=REPO / "target" / "hal" / "hal_manifest.json")
    args = parser.parse_args(argv)

    descriptor = migrate_descriptor(
        json.loads(args.descriptor.read_text(encoding="utf-8")))
    routing = migrate_routing(
        json.loads(args.routing.read_text(encoding="utf-8")))
    # Integration-time rate assignments (G-2) apply before codegen, so the
    # forced solver rate and the dispatcher tables agree by construction.
    descriptor = apply_rate_assignments(
        descriptor, routing.get("rate_assignments", {}))

    print("generating RTE...")
    generate_rte(descriptor, routing, load_hal_manifest(args.hal_manifest),
                 args.out)

    print("running ERT codegen...")
    session = MatlabEngineSession()
    session.start()
    try:
        generated = codegen_all(session, descriptor, args.slx_dir,
                                args.out / "ert")
    finally:
        session.stop()

    counts = arrange(descriptor, generated, args.out)
    adapters = generate_ert_adapters(descriptor, args.out / "models")

    print(f"arranged {counts['fast']} fast + {counts['slow']} slow ERT "
          f"source(s), {counts['headers']} header(s)")
    print(f"emitted {len(adapters)} adapter(s)")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
