#!/usr/bin/env python3
"""Generate a full-size synthetic workspace (Phase 8 scale validation).

The SRS scopes the toolchain at 20–30 independent models (§1.1), so the
release evidence has to come from a workspace that size rather than the
two-model fixture. This builds one: N models spread across all three rate
groups, chained producer→consumer so the mesh really crosses cores, plus
stand-in step implementations, then emits descriptor + routing + sources.

Usage: tools/make_scale_workspace.py OUTDIR [--models 28]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "host"))

from picodesk.rtegen.generator import generate_rte
from picodesk.rtegen.routing import load_hal_manifest

GROUPS = ["fast_1ms", "slow_10ms", "slow_100ms"]
RATES = {"fast_1ms": 0.001, "slow_10ms": 0.01, "slow_100ms": 0.1}

IMPL = """/* Stand-in for ERT-generated {name} step code (scale fixture). */

#include "pd_{name}_io.h"

static int32_t s_state;

void pd_{name}_init(void) {{
    s_state = {seed};
}}

void PD_{upper}_STEP(const pd_{name}_in_t *in, pd_{name}_out_t *out) {{
    /* Integer-only (MAT-002): a decaying accumulator so every model both
     * responds to its input and keeps moving on its own. */
    s_state += (int32_t) in->u - (s_state >> 4) + {seed};
    if (s_state > 32000) s_state = 32000;
    if (s_state < -32000) s_state = -32000;
    out->y = (int16_t) s_state;
}}
"""


def build(out_dir: Path, count: int) -> dict:
    models: dict[str, dict] = {}
    connections: list[dict] = []

    for index in range(count):
        name = f"Model{index:02d}"
        group = GROUPS[index % 3]
        # Model00 is fed by the ADC, so its inport carries the HAL's type;
        # the type filter (GUI-008) rejects the binding otherwise.
        in_type = "uint16" if index == 0 else "int16"
        models[name] = {
            "file": f"{name}.slx",
            "slx_sha256": f"{index:064x}",
            "base_rate_s": RATES[group],
            "rate_group": group,
            "inports": [{"name": "u", "data_type": in_type, "width": 1}],
            "outports": [{"name": "y", "data_type": "int16", "width": 1}],
            "internal_types": ["int16", "int32"],
        }
        if index:
            # Chaining by index cycles through the rate groups, so roughly
            # two thirds of the edges cross a rate boundary and exercise the
            # seqlock buses (RTE-004) rather than plain stores.
            connections.append({"producer": f"Model{index - 1:02d}.y",
                                "consumer": f"{name}.u"})

    # One HAL binding into the fast loop, so GUI-006 is on the scale path too.
    connections.append({"producer": "hal.hal_adc_read",
                        "consumer": "Model00.u", "hal_arg": 0})
    connections[:] = [c for c in connections if c["consumer"] != "Model00.u"
                      or c["producer"].startswith("hal.")]

    descriptor = {"schema_version": 1, "models": models}
    routing = {"schema_version": 1, "workspace": "scale",
               "connections": connections}

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "descriptor.json").write_text(
        json.dumps(descriptor, indent=2, sort_keys=True) + "\n")
    (out_dir / "routing.json").write_text(
        json.dumps(routing, indent=2, sort_keys=True) + "\n")

    gen = out_dir / "gen"
    generate_rte(descriptor, routing,
                 load_hal_manifest(REPO / "target" / "hal" / "hal_manifest.json"),
                 gen)
    impl_dir = gen / "models"
    impl_dir.mkdir(exist_ok=True)
    for index, name in enumerate(sorted(models)):
        (impl_dir / f"{name}.c").write_text(
            IMPL.format(name=name, upper=name.upper(), seed=index + 1), encoding="utf-8")

    return {"models": len(models), "connections": len(connections),
            "generated": gen}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--models", type=int, default=28)
    args = parser.parse_args(argv)

    summary = build(args.outdir, args.models)
    print(f"{summary['models']} models, {summary['connections']} connections")
    print(summary["generated"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
