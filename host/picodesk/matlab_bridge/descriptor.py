"""Model descriptor schema v2 — the frozen contract (Phase 4, MAT-001).

The extractor emits one monolithic descriptor for the whole workspace; the
routing layer, RTE generator, and GUI all consume exactly this shape. Bump
SCHEMA_VERSION and add a migration whenever the shape changes (GUI-003
discipline applies to this schema too).

v1 -> v2 (the examples/ workspace gaps, G-2/G-4/G-7):
  - `rate_group` may be null: a rate-agnostic model (everything inherited)
    carries no group of its own and gets one assigned at integration time.
  - optional `dictionaries`: the model's data-dictionary closure with file
    hashes, so the cache gate covers dictionary edits (GUI-001).
  - optional `interface_violations` / `dictionary_parameters`: diagnostics
    from checking ports against the dictionary-declared interface catalogue,
    and parameters that ERT will inline (untunable) under the current policy.
Migration: v1 descriptors are valid v2 descriptors with none of the new
fields — `migrate_descriptor` stamps the version and nothing else.
"""

from __future__ import annotations

import json
from typing import Any

SCHEMA_VERSION = 2

#: Data types the VFB routes. `single`/`double` are representable so slow-loop
#: models can use them, but they are a hard error in fast-loop models
#: (MAT-002) and never eligible for fast-path routing.
DATA_TYPES = (
    "boolean",
    "int8",
    "uint8",
    "int16",
    "uint16",
    "int32",
    "uint32",
    "single",
    "double",
)

FLOAT_TYPES = ("single", "double")

#: Rate groups of the target dispatcher (RTE-002).
RATE_GROUPS = ("fast_1ms", "slow_10ms", "slow_100ms")

RATE_GROUP_PERIOD_S = {
    "fast_1ms": 0.001,
    "slow_10ms": 0.010,
    "slow_100ms": 0.100,
}

_PORT_SCHEMA = {
    "type": "object",
    "required": ["name", "data_type", "width"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "pattern": r"^[A-Za-z_]\w*$"},
        "data_type": {"enum": list(DATA_TYPES)},
        "width": {"type": "integer", "minimum": 1, "maximum": 64},
        # Fixed-point scaling: real = raw * slope + bias (GUI-008 matches on
        # the full triple, not just the storage type).
        "slope": {"type": "number"},
        "bias": {"type": "number"},
        "unit": {"type": "string"},
    },
}

MODEL_SCHEMA = {
    "type": "object",
    "required": ["file", "slx_sha256", "base_rate_s", "rate_group",
                 "inports", "outports"],
    "additionalProperties": False,
    "properties": {
        "file": {"type": "string"},
        "slx_sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
        "base_rate_s": {"type": "number", "exclusiveMinimum": 0},
        # null = rate-agnostic model awaiting an integration-time assignment
        # (G-2). Routing refuses to touch such a model until one is applied.
        "rate_group": {"enum": [*RATE_GROUPS, None]},
        "inports": {"type": "array", "items": _PORT_SCHEMA},
        "outports": {"type": "array", "items": _PORT_SCHEMA},
        # From the compiled-model metrics: every internal data type in use.
        # Basis of the MAT-002 fast-loop float rejection.
        "internal_types": {
            "type": "array",
            "items": {"enum": list(DATA_TYPES)},
        },
        # Data-dictionary closure with content hashes: part of the staleness
        # gate, so a dictionary edit invalidates the cache (GUI-001, G-4).
        "dictionaries": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["file", "sha256"],
                "additionalProperties": False,
                "properties": {
                    "file": {"type": "string"},
                    "sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
                },
            },
        },
        # Ports contradicting the dictionary-declared interface catalogue
        # (G-7), and dictionary parameters ERT will inline untunably (G-8).
        # Diagnostics, not blockers: surfaced by the GUI and the console.
        "interface_violations": {"type": "array", "items": {"type": "string"}},
        "dictionary_parameters": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "data_type": {"type": "string"},
                    "dictionary": {"type": "string"},
                },
            },
        },
    },
}

DESCRIPTOR_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PicoDesk workspace model descriptor",
    "type": "object",
    "required": ["schema_version", "models"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "models": {
            "type": "object",
            "propertyNames": {"pattern": r"^[A-Za-z_]\w*$"},
            "additionalProperties": MODEL_SCHEMA,
        },
    },
}


def validate_descriptor(descriptor: dict[str, Any]) -> None:
    """Raise jsonschema.ValidationError when the descriptor is malformed."""
    import jsonschema

    jsonschema.validate(descriptor, DESCRIPTOR_SCHEMA)


def dump_canonical(descriptor: dict[str, Any]) -> str:
    """Serialize deterministically: identical input -> identical bytes, so
    descriptor diffs and downstream hash gates are meaningful (NFR-2)."""
    return json.dumps(descriptor, indent=2, sort_keys=True) + "\n"


def rate_group_for(base_rate_s: float) -> str:
    """Map a model base rate onto a dispatcher rate group (RTE-002)."""
    group = rate_group_or_none(base_rate_s)
    if group is None:
        raise ValueError(
            f"base rate {base_rate_s}s does not match a dispatcher rate group "
            f"{sorted(RATE_GROUP_PERIOD_S.values())} (RTE-002)"
        )
    return group


def rate_group_or_none(base_rate_s: float) -> str | None:
    """Like rate_group_for, but a non-matching rate means "rate-agnostic
    model, assign at integration time" (G-2) rather than an error. MATLAB
    invents a rate (0.2 s) for a fully inherited model, so an unmatched
    compiled rate is the normal signature of the interface-first development
    cycle, not a broken model."""
    for group, period in RATE_GROUP_PERIOD_S.items():
        if abs(base_rate_s - period) < 1e-9:
            return group
    return None


def migrate_descriptor(descriptor: dict[str, Any]) -> dict[str, Any]:
    """Upgrade an older descriptor in place to the current schema.

    v1 -> v2 is purely additive (every v1 model is a valid v2 model), so the
    migration stamps the version and nothing else. Unknown future versions
    raise rather than guessing.
    """
    version = descriptor.get("schema_version")
    if version == SCHEMA_VERSION:
        return descriptor
    if version == 1:
        descriptor["schema_version"] = SCHEMA_VERSION
        return descriptor
    raise ValueError(
        f"descriptor schema v{version} is newer than this toolchain "
        f"understands (v{SCHEMA_VERSION}) — upgrade PicoDesk")
