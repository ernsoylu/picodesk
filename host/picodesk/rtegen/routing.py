"""Routing configuration schema v2 + VFB validation rules (Phase 5).

The routing config is the versioned artifact the GUI saves (GUI-003): a flat
list of producer->consumer connections over the full mesh — any ASW model's
outport may feed any other ASW model's inport, in either direction, plus
HAL endpoints from hal_manifest.json (GUI-006).

v1 -> v2 (G-2): optional `rate_assignments` maps a rate-agnostic model to
the dispatcher group the integrator chose. Rate assignment is an
INTEGRATION decision, so it lives in the routing artifact, not the model:
the same model can run fast in one workspace and slow in another.
`migrate_routing` upgrades v1 configs (no assignments) transparently.

Validation enforced here, before any code is generated:
  - endpoints exist in the descriptor / HAL manifest
  - every routed model has a rate group — modelled or assigned (RTE-002)
  - exactly one writer per consumer inport; for HAL endpoints the identity
    is (function, hal_arg) — per pin/channel, not per function (GUI-009)
  - exact data type + width + fixed-point scaling match (GUI-008)
  - HAL functions bound to fast-loop models must be isr_safe (GUI-006)
Edges are then classified (GUI-010): same rate group -> direct signal-store
copy; cross rate group -> ZOH over a bounded seqlock bus (RTE-004).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROUTING_SCHEMA_VERSION = 2

ROUTING_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PicoDesk routing configuration",
    "type": "object",
    "required": ["schema_version", "connections"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"const": ROUTING_SCHEMA_VERSION},
        "workspace": {"type": "string"},
        # Integration-time rate groups for rate-agnostic models (G-2).
        "rate_assignments": {
            "type": "object",
            "propertyNames": {"pattern": r"^[A-Za-z_]\w*$"},
            "additionalProperties": {
                "enum": ["fast_1ms", "slow_10ms", "slow_100ms"]},
        },
        "connections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["producer", "consumer"],
                "additionalProperties": False,
                "properties": {
                    # "Model.port" or "hal.function_name"
                    "producer": {"type": "string", "pattern": r"^\w+\.\w+$"},
                    "consumer": {"type": "string", "pattern": r"^\w+\.\w+$"},
                    # Argument for HAL endpoints (ADC channel, GPIO pin, ...)
                    "hal_arg": {"type": "integer", "minimum": 0},
                },
            },
        },
    },
}


class RoutingError(ValueError):
    """A validation failure the GUI surfaces verbatim."""


def migrate_routing(routing: dict[str, Any]) -> dict[str, Any]:
    """Upgrade an older routing config in place (GUI-003 discipline).

    v1 -> v2 adds the empty `rate_assignments` map. Unknown future versions
    raise rather than being guessed at.
    """
    version = routing.get("schema_version")
    if version == ROUTING_SCHEMA_VERSION:
        routing.setdefault("rate_assignments", {})
        return routing
    if version == 1:
        routing["schema_version"] = ROUTING_SCHEMA_VERSION
        routing.setdefault("rate_assignments", {})
        return routing
    raise RoutingError(
        f"routing schema v{version} is newer than this toolchain "
        f"understands (v{ROUTING_SCHEMA_VERSION}) — upgrade PicoDesk")


@dataclass(frozen=True)
class Endpoint:
    kind: str          # "model" | "hal"
    owner: str         # model name or HAL function name
    port: str          # port name (models) == owner for HAL
    data_type: str
    width: int
    slope: float
    bias: float
    rate_group: str | None  # None for HAL (inherits the peer's group)
    isr_safe: bool = True
    hal_arg: int = 0


@dataclass(frozen=True)
class Edge:
    producer: Endpoint
    consumer: Endpoint
    mechanism: str     # "direct" | "zoh_seqlock" (GUI-010)


def load_hal_manifest(path: Path) -> dict[str, dict[str, Any]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {f["name"]: f for f in manifest["functions"]}


def validate_routing_schema(routing: dict[str, Any]) -> None:
    import jsonschema

    jsonschema.validate(routing, ROUTING_SCHEMA)


def _model_endpoint(descriptor: dict[str, Any], ref: str, direction: str) -> Endpoint:
    owner, port_name = ref.split(".", 1)
    model = descriptor["models"].get(owner)
    if model is None:
        raise RoutingError(f"{ref}: model {owner!r} is not in the workspace")
    if model["rate_group"] is None:
        raise RoutingError(
            f"{ref}: model {owner!r} is rate-agnostic and has no rate group "
            f"assigned — assign one (rate_assignments / the models page) "
            f"before routing it (RTE-002)")
    ports = model["outports"] if direction == "producer" else model["inports"]
    for port in ports:
        if port["name"] == port_name:
            return Endpoint(
                kind="model",
                owner=owner,
                port=port_name,
                data_type=port["data_type"],
                width=port["width"],
                slope=float(port.get("slope", 1.0)),
                bias=float(port.get("bias", 0.0)),
                rate_group=model["rate_group"],
            )
    side = "outport" if direction == "producer" else "inport"
    raise RoutingError(f"{ref}: model {owner!r} has no {side} {port_name!r}")


def _hal_endpoint(hal: dict[str, dict[str, Any]], ref: str, direction: str,
                  hal_arg: int) -> Endpoint:
    _, func = ref.split(".", 1)
    entry = hal.get(func)
    if entry is None:
        raise RoutingError(f"{ref}: HAL manifest has no function {func!r} (GUI-006)")
    if entry["direction"] != direction:
        raise RoutingError(
            f"{ref}: HAL function {func!r} is a {entry['direction']}, bound as "
            f"{direction}"
        )
    return Endpoint(
        kind="hal",
        owner=func,
        port=func,
        data_type=entry["data_type"],
        width=1,
        slope=1.0,
        bias=0.0,
        rate_group=None,
        isr_safe=bool(entry.get("isr_safe", False)),
        hal_arg=hal_arg,
    )


def _resolve(descriptor: dict[str, Any], hal: dict[str, dict[str, Any]],
             ref: str, direction: str, hal_arg: int) -> Endpoint:
    owner = ref.split(".", 1)[0]
    if owner == "hal":
        return _hal_endpoint(hal, ref, direction, hal_arg)
    return _model_endpoint(descriptor, ref, direction)


def resolve_routing(
    descriptor: dict[str, Any],
    routing: dict[str, Any],
    hal_manifest: dict[str, dict[str, Any]],
) -> list[Edge]:
    """Validate the full config and return classified edges."""
    routing = migrate_routing(dict(routing))
    validate_routing_schema(routing)

    assignments = routing.get("rate_assignments", {})
    if assignments:
        # Applying re-runs MAT-002 per assignment; a float model assigned to
        # the fast group fails here, before any code exists.
        from picodesk.matlab_bridge.extractor import apply_rate_assignments
        descriptor = apply_rate_assignments(descriptor, assignments)

    edges: list[Edge] = []
    writers: dict[str, str] = {}  # writer identity -> producer ref (GUI-009)

    for conn in routing["connections"]:
        prod_ref, cons_ref = conn["producer"], conn["consumer"]
        hal_arg = int(conn.get("hal_arg", 0))
        producer = _resolve(descriptor, hal_manifest, prod_ref, "producer", hal_arg)
        consumer = _resolve(descriptor, hal_manifest, cons_ref, "consumer", hal_arg)

        if producer.kind == "hal" and consumer.kind == "hal":
            raise RoutingError(
                f"{prod_ref} -> {cons_ref}: HAL-to-HAL connections carry no "
                f"model signal and are not routable"
            )

        # Single writer (GUI-009). For a HAL consumer the endpoint is the
        # (function, hal_arg) pair — hal_gpio_write on two different pins is
        # two endpoints (G-5); the same pin twice is a genuine double-drive.
        writer_key = (f"{cons_ref}[{hal_arg}]" if consumer.kind == "hal"
                      else cons_ref)
        if writer_key in writers:
            raise RoutingError(
                f"{writer_key} already bound to {writers[writer_key]} — "
                f"consumers accept exactly one producer (GUI-009)"
            )
        writers[writer_key] = prod_ref

        # Array signals route like scalars; the generator copies them by
        # memcpy and GUI-008 below still demands an exact width match.
        # Strict type filter (GUI-008): storage type, width, and scaling
        # must all match exactly.
        mismatches = []
        if producer.data_type != consumer.data_type:
            mismatches.append(
                f"type {producer.data_type} != {consumer.data_type}")
        if producer.width != consumer.width:
            mismatches.append(f"width {producer.width} != {consumer.width}")
        if (producer.slope, producer.bias) != (consumer.slope, consumer.bias):
            mismatches.append(
                f"scaling ({producer.slope},{producer.bias}) != "
                f"({consumer.slope},{consumer.bias})")
        if mismatches:
            raise RoutingError(
                f"{prod_ref} -> {cons_ref}: {'; '.join(mismatches)} (GUI-008)")

        # HAL ISR-safety (GUI-006): a HAL endpoint paired with a fast-loop
        # model executes inside the core 0 ISR.
        model_side = producer if producer.kind == "model" else consumer
        hal_side = producer if producer.kind == "hal" else (
            consumer if consumer.kind == "hal" else None)
        if hal_side is not None and model_side.rate_group == "fast_1ms" \
                and not hal_side.isr_safe:
            raise RoutingError(
                f"{prod_ref} -> {cons_ref}: HAL function {hal_side.owner!r} "
                f"is not ISR-safe but is bound to a fast-loop model (GUI-006)")

        # Classification (GUI-010): HAL inherits the model's group.
        prod_group = producer.rate_group or model_side.rate_group
        cons_group = consumer.rate_group or model_side.rate_group
        mechanism = "direct" if prod_group == cons_group else "zoh_seqlock"
        edges.append(Edge(producer=producer, consumer=consumer,
                          mechanism=mechanism))

    return edges
