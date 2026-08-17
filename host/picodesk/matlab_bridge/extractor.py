"""Batch .slx extraction to the monolithic descriptor (MAT-001).

Every .slx is content-hashed (sha256). A cache maps hash -> extracted model
descriptor, so an unchanged model costs zero MATLAB round trips on re-scan —
the hash gate that keeps routing-only changes inside the NFR-2 budget and the
staleness signal the GUI surfaces (GUI-001).

Fast-loop models containing `single`/`double` anywhere (ports or compiled
internal types) fail extraction with a pointed hard error (MAT-002): the
RP2040 has no FPU and software float has no place in a 1 ms loop.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from picodesk.matlab_bridge.descriptor import (
    FLOAT_TYPES,
    SCHEMA_VERSION,
    rate_group_for,
    validate_descriptor,
)


class FloatInFastLoopError(RuntimeError):
    """MAT-002 hard build error."""

    def __init__(self, model: str, offenders: list[str]) -> None:
        super().__init__(
            f"model {model!r} is mapped to the fast loop but uses software "
            f"float: {', '.join(offenders)} — the RP2040 has no FPU; convert "
            f"to fixed-point or move the model to a slower rate group "
            f"(MAT-002, hard error)"
        )
        self.model = model
        self.offenders = offenders


def hash_slx(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ExtractionCache:
    """hash -> per-model descriptor, persisted as JSON (NFR-2 gate)."""

    def __init__(self, cache_file: Path) -> None:
        self._file = cache_file
        self._entries: dict[str, dict[str, Any]] = {}
        if cache_file.is_file():
            self._entries = json.loads(cache_file.read_text(encoding="utf-8"))

    def get(self, slx_hash: str) -> dict[str, Any] | None:
        return self._entries.get(slx_hash)

    def put(self, slx_hash: str, model_descriptor: dict[str, Any]) -> None:
        self._entries[slx_hash] = model_descriptor

    def save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            json.dumps(self._entries, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def check_fast_loop_types(name: str, model: dict[str, Any]) -> None:
    """Enforce MAT-002 on one model descriptor."""
    if model["rate_group"] != "fast_1ms":
        return
    offenders: list[str] = []
    for direction in ("inports", "outports"):
        for port in model[direction]:
            if port["data_type"] in FLOAT_TYPES:
                offenders.append(f"{direction[:-1]} {port['name']}: {port['data_type']}")
    for internal in model.get("internal_types", []):
        if internal in FLOAT_TYPES:
            offenders.append(f"internal signal/state of type {internal}")
    if offenders:
        raise FloatInFastLoopError(name, offenders)


def extract_models(
    session: Any,
    slx_dir: Path,
    cache: ExtractionCache,
) -> tuple[dict[str, Any], dict[str, bool]]:
    """Scan slx_dir, extract changed models through the MATLAB session, and
    return (monolithic descriptor, {model_name: was_cache_hit}).

    `session` needs one method: call("picodesk_extract", "<path>") -> JSON
    string (MatlabEngineSession in production, a fake in tests).
    """
    models: dict[str, Any] = {}
    cache_hits: dict[str, bool] = {}

    for slx in sorted(slx_dir.glob("*.slx")):
        name = slx.stem
        slx_hash = hash_slx(slx)

        cached = cache.get(slx_hash)
        if cached is not None:
            model = dict(cached)
            cache_hits[name] = True
        else:
            raw = session.call("picodesk_extract", str(slx))
            model = json.loads(raw)
            model["rate_group"] = rate_group_for(model["base_rate_s"])
            cache_hits[name] = False

        model["file"] = slx.name
        model["slx_sha256"] = slx_hash
        check_fast_loop_types(name, model)  # enforced on hits too
        if not cache_hits[name]:
            cache.put(slx_hash, model)
        models[name] = model

    descriptor = {"schema_version": SCHEMA_VERSION, "models": models}
    validate_descriptor(descriptor)
    return descriptor, cache_hits
