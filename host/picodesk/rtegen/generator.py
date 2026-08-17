"""RTE code generation (Phase 5): descriptor + routing -> C sources.

Emits, into an output directory:
  - rte_gen.h        bus/store/telemetry structs + the firmware-facing API
  - rte_gen.c        the generated dispatcher: core 0 fast ISR + core 1 rate
                     tasks (RTE-002), copy-in shadows (RTE-001), bounded
                     seqlock buses for every cross-rate-group direction
                     (RTE-004), coherent DAQ push (RTE-005), task/stack table
                     (BLD-005); every fast-path emission is SRAM-resident
                     (BLD-001)
  - pd_<model>_io.h  per-model IO structs + step/init prototypes — the seam
                     ERT model code (or a handwritten stand-in) implements

All emitted symbols carry the pd_<model> prefix (MAT-003); generation is
deterministic (sorted iteration, no timestamps) so re-running on unchanged
inputs is byte-identical (BLD-008 discipline).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from picodesk.rtegen.routing import Edge, RoutingError, resolve_routing

C_TYPES = {
    "boolean": ("uint8_t", 1),
    "int8": ("int8_t", 1),
    "uint8": ("uint8_t", 1),
    "int16": ("int16_t", 2),
    "uint16": ("uint16_t", 2),
    "int32": ("int32_t", 4),
    "uint32": ("uint32_t", 4),
    "single": ("float", 4),
    "double": ("double", 8),
}

#: Rate groups in dispatch order; fast runs in the core 0 ISR, the rest are
#: core 1 tasks released by tick dividers (RTE-002).
GROUPS = ("fast_1ms", "slow_10ms", "slow_100ms")
GROUP_DIVIDER = {"slow_10ms": 10, "slow_100ms": 100}
GROUP_PRIORITY = {"slow_10ms": 4, "slow_100ms": 3}  # rate-monotonic (BLD-005)

SEQLOCK_MAX_BYTES = 64  # RTE_SEQLOCK_MAX_WORDS * 4 (rte_seqlock.h)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _check_symbol_hygiene(models: dict[str, Any]) -> None:
    """MAT-003: prefixed symbols must stay collision-free across 30 models."""
    seen: dict[str, str] = {}
    for name in models:
        key = name.lower()
        if key in seen:
            raise RoutingError(
                f"models {seen[key]!r} and {name!r} collide case-insensitively "
                f"in generated symbol prefixes (MAT-003)")
        seen[key] = name


def _ctype(port: dict[str, Any]) -> str:
    base, _ = C_TYPES[port["data_type"]]
    return base


def _padded_fields(fields: list[dict[str, Any]]) -> tuple[list[dict], int]:
    """Order bus fields largest-first and pad the struct to a word multiple
    (seqlock payloads copy whole words)."""
    ordered = sorted(
        fields, key=lambda f: (-C_TYPES[f["data_type"]][1], f["field"]))
    size = 0
    for f in ordered:
        fsize = C_TYPES[f["data_type"]][1]
        size = (size + fsize - 1) // fsize * fsize + fsize  # natural alignment
    pad = (-size) % 4
    return ordered, pad


def build_context(descriptor: dict[str, Any], edges: list[Edge]) -> dict[str, Any]:
    models = descriptor["models"]
    _check_symbol_hygiene(models)

    groups: dict[str, dict[str, Any]] = {
        g: {"name": g, "models": [], "hal_writes": []} for g in GROUPS
    }
    for name in sorted(models):
        m = models[name]
        groups[m["rate_group"]]["models"].append({
            "name": name,
            "inports": [
                {**p, "ctype": _ctype(p)} for p in m["inports"]],
            "outports": [
                {**p, "ctype": _ctype(p)} for p in m["outports"]],
        })

    # Buses: one seqlock-protected struct per (producer group -> consumer
    # group) direction that carries at least one signal (RTE-004).
    buses: dict[tuple[str, str], dict[str, Any]] = {}
    # Input resolution: (model, inport) -> source description.
    inputs: dict[tuple[str, str], dict[str, Any]] = {}

    for edge in edges:
        prod, cons = edge.producer, edge.consumer
        model_side = prod if prod.kind == "model" else cons
        prod_group = prod.rate_group or model_side.rate_group
        cons_group = cons.rate_group or model_side.rate_group

        if cons.kind == "hal":
            groups[prod_group]["hal_writes"].append({
                "func": cons.owner,
                "arg": cons.hal_arg,
                "model": prod.owner,
                "port": prod.port,
            })
            continue

        key = (cons.owner, cons.port)
        if prod.kind == "hal":
            inputs[key] = {"kind": "hal", "func": prod.owner,
                           "arg": prod.hal_arg}
        elif edge.mechanism == "direct":
            inputs[key] = {"kind": "store", "group": prod_group,
                           "model": prod.owner, "port": prod.port}
        else:
            bus_key = (prod_group, cons_group)
            bus = buses.setdefault(bus_key, {
                "name": f"bus_{prod_group}__to__{cons_group}",
                "src": prod_group,
                "dst": cons_group,
                "fields": [],
            })
            field = f"{prod.owner}_{prod.port}"
            if not any(f["field"] == field for f in bus["fields"]):
                bus["fields"].append({
                    "field": field,
                    "model": prod.owner,
                    "port": prod.port,
                    "data_type": prod.data_type,
                    "ctype": _ctype({"data_type": prod.data_type}),
                })
            inputs[key] = {"kind": "bus", "bus": bus["name"], "field": field}

    bus_list = []
    for bus_key in sorted(buses):
        bus = buses[bus_key]
        ordered, pad = _padded_fields(bus["fields"])
        size = sum(C_TYPES[f["data_type"]][1] for f in ordered) + pad
        if size > SEQLOCK_MAX_BYTES:
            raise RoutingError(
                f"{bus['name']}: {size} bytes of cross-rate signals exceed "
                f"the {SEQLOCK_MAX_BYTES}-byte seqlock payload bound "
                f"(RTE-004) — split the routing or coarsen the signal set")
        bus["fields"] = ordered
        bus["pad"] = pad
        bus_list.append(bus)

    # Attach resolved input sources to every model inport (unbound -> zero,
    # the RTE-004 boot default).
    for g in groups.values():
        for m in g["models"]:
            for p in m["inports"]:
                p["source"] = inputs.get(
                    (m["name"], p["name"]), {"kind": "zero"})

    # Debug signals for the emitted stats line: first fast outport and first
    # slow outport keep the Renode E2E assertions concrete.
    debug = []
    for gname in GROUPS:
        for m in groups[gname]["models"]:
            if m["outports"]:
                debug.append({"group": gname, "model": m["name"],
                              "port": m["outports"][0]["name"],
                              "ctype": m["outports"][0]["ctype"]})
                break

    daq_fields = [
        {"model": m["name"], "port": p["name"], "ctype": p["ctype"]}
        for m in groups["fast_1ms"]["models"] for p in m["outports"]
    ]

    def _used(g: dict[str, Any]) -> bool:
        return bool(g["models"] or g["hal_writes"])

    return {
        "groups": [groups[g] for g in GROUPS if _used(groups[g])],
        "fast": groups["fast_1ms"],
        "slow_groups": [groups[g] for g in GROUPS[1:] if _used(groups[g])],
        "buses": bus_list,
        "dividers": GROUP_DIVIDER,
        "priorities": GROUP_PRIORITY,
        "daq_fields": daq_fields,
        "debug_signals": debug,
    }


def generate_rte(descriptor: dict[str, Any], routing: dict[str, Any],
                 hal_manifest: dict[str, dict[str, Any]],
                 out_dir: Path) -> list[Path]:
    """Validate, build the context, and render all generated sources."""
    import jinja2

    edges = resolve_routing(descriptor, routing, hal_manifest)
    context = build_context(descriptor, edges)

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for template, filename in (("rte_gen.h.j2", "rte_gen.h"),
                               ("rte_gen.c.j2", "rte_gen.c")):
        path = out_dir / filename
        path.write_text(env.get_template(template).render(**context),
                        encoding="utf-8")
        written.append(path)

    io_template = env.get_template("model_io.h.j2")
    for group in context["groups"]:
        for model in group["models"]:
            path = out_dir / f"pd_{model['name']}_io.h"
            path.write_text(io_template.render(model=model), encoding="utf-8")
            written.append(path)

    return written


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Generate the PicoDesk RTE from descriptor + routing")
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--routing", type=Path, required=True)
    parser.add_argument("--hal-manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    from picodesk.rtegen.routing import load_hal_manifest

    descriptor = json.loads(args.descriptor.read_text(encoding="utf-8"))
    routing = json.loads(args.routing.read_text(encoding="utf-8"))
    hal = load_hal_manifest(args.hal_manifest)
    written = generate_rte(descriptor, routing, hal, args.out)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
