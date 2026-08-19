"""Batch .slx extraction to the monolithic descriptor (MAT-001).

Every .slx is content-hashed (sha256) together with its data-dictionary
closure. A cache maps hash -> extracted model descriptor, so an unchanged
model costs zero MATLAB round trips on re-scan — the hash gate that keeps
routing-only changes inside the NFR-2 budget and the staleness signal the GUI
surfaces (GUI-001). A dictionary edit invalidates every model attached to
that dictionary (G-4), which is exactly the semantics a shared interface
dictionary needs.

Fast-loop models containing `single`/`double` anywhere (ports or compiled
internal types) fail extraction with a pointed hard error (MAT-002): the
RP2040 has no FPU and software float has no place in a 1 ms loop.

A model MATLAB cannot process is a per-model diagnosis, not a batch failure
(G-9): its error lands in the returned `errors` map and the scan continues.
Only MAT-002 aborts — it is a hard build error by requirement — and only a
genuinely dead engine raises MatlabSessionError.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from picodesk.matlab_bridge.descriptor import (
    FLOAT_TYPES,
    RATE_GROUPS,
    SCHEMA_VERSION,
    rate_group_or_none,
    validate_descriptor,
)
from picodesk.matlab_bridge.session import MatlabCallError


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


def _normalize_matlab_json(model: dict[str, Any]) -> dict[str, Any]:
    """MATLAB's jsonencode collapses 1-element struct/cell arrays to scalars:
    a single-port model arrives as an object, a single internal type as a
    string. Re-shape everything back to lists."""
    for key in ("inports", "outports"):
        value = model.get(key)
        if value is None:
            model[key] = []
        elif isinstance(value, dict):
            model[key] = [value]
    internal = model.get("internal_types")
    if isinstance(internal, str):
        model["internal_types"] = [internal]
    elif internal is None:
        model["internal_types"] = []
    for key in ("dictionaries", "interface_catalog"):
        value = model.get(key)
        if value is None:
            model[key] = []
        elif isinstance(value, (dict, str)):
            model[key] = [value]
    return model


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


def apply_rate_assignments(descriptor: dict[str, Any],
                           assignments: dict[str, str]) -> dict[str, Any]:
    """Apply integration-time rate-group assignments (G-2, RTE-002).

    Returns a new descriptor; the input is not mutated. Assigning a model to
    the fast group re-runs the MAT-002 check against its port and internal
    types — a `single`-carrying model assigned to fast_1ms is exactly as
    hard an error as a modelled 1 ms float.
    """
    result = json.loads(json.dumps(descriptor))
    for name, group in assignments.items():
        model = result["models"].get(name)
        if model is None:
            raise ValueError(
                f"rate assignment for unknown model {name!r} — stale "
                f"assignment after a model was removed?")
        if group not in RATE_GROUPS:
            raise ValueError(
                f"{name}: {group!r} is not a rate group {list(RATE_GROUPS)}")
        if model["rate_group"] not in (None, group):
            raise ValueError(
                f"{name}: modelled rate group {model['rate_group']} cannot "
                f"be overridden to {group} — the model's own rate wins; "
                f"change the model, not the assignment (RTE-002)")
        model["rate_group"] = group
        check_fast_loop_types(name, model)
    return result


def _relative_to(path_str: str, base: Path) -> str:
    """Dictionary paths relative to the workspace when possible — the
    descriptor should not bake in one machine's absolute paths."""
    path = Path(path_str)
    try:
        return str(path.relative_to(base.resolve()))
    except ValueError:
        return path_str


def _hash_dictionaries(model: dict[str, Any], slx_dir: Path,
                       raw_paths: list[str]) -> None:
    """Record the dictionary closure with content hashes (G-4)."""
    entries = []
    for raw in raw_paths:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = slx_dir / raw
        if not candidate.is_file():
            raise FileNotFoundError(
                f"data dictionary {raw!r} referenced by the model was not "
                f"found — dictionaries must live with the workspace")
        entries.append({"file": _relative_to(raw, slx_dir),
                        "sha256": hash_slx(candidate)})
    model["dictionaries"] = sorted(entries, key=lambda e: e["file"])


def _dictionaries_fresh(model: dict[str, Any], slx_dir: Path) -> bool:
    """A cache hit is only a hit while the recorded dictionary closure is
    byte-identical too (GUI-001, G-4)."""
    for entry in model.get("dictionaries", []):
        candidate = Path(entry["file"])
        if not candidate.is_absolute():
            candidate = slx_dir / candidate
        if not candidate.is_file() or hash_slx(candidate) != entry["sha256"]:
            return False
    return True


def _check_interface_catalog(model: dict[str, Any],
                             catalog: list[dict[str, Any]]) -> None:
    """Diagnose ports contradicting the dictionary-declared interface (G-7)
    and record dictionary parameters, which ERT inlines untunably under the
    current policy (G-8). Neither blocks extraction; both are for the GUI
    and the console to surface at the source instead of as a late,
    misattributed bind error."""
    entries = [e for e in catalog if isinstance(e, dict)]
    signals = {e["name"]: e for e in entries
               if e.get("class") == "Simulink.Signal"}
    parameters = [
        {"name": e["name"], "data_type": e.get("data_type", ""),
         "dictionary": e.get("dictionary", "")}
        for e in entries if e.get("class") == "Simulink.Parameter"
    ]
    violations: list[str] = []
    for direction in ("inports", "outports"):
        for port in model[direction]:
            declared = signals.get(port["name"])
            if declared is None:
                continue
            declared_type = declared.get("data_type", "")
            if declared_type and declared_type != port["data_type"]:
                violations.append(
                    f"{direction[:-1]} {port['name']} compiles as "
                    f"{port['data_type']} but {declared['dictionary']} "
                    f"declares {declared_type}")
    if violations:
        model["interface_violations"] = violations
    if parameters:
        model["dictionary_parameters"] = parameters


def extract_models(
    session: Any,
    slx_dir: Path,
    cache: ExtractionCache,
) -> tuple[dict[str, Any], dict[str, bool], dict[str, str]]:
    """Scan slx_dir, extract changed models through the MATLAB session, and
    return (monolithic descriptor, {model: was_cache_hit}, {model: error}).

    `session` needs one method: call("picodesk_extract", "<path>") -> JSON
    string (MatlabEngineSession in production, a fake in tests). A model
    MATLAB rejects lands in the error map and the scan continues (G-9);
    MAT-002 still aborts, because it is a hard build error by requirement.
    """
    models: dict[str, Any] = {}
    cache_hits: dict[str, bool] = {}
    errors: dict[str, str] = {}

    for slx in sorted(slx_dir.glob("*.slx")):
        name = slx.stem
        slx_hash = hash_slx(slx)

        cached = cache.get(slx_hash)
        if cached is not None and _dictionaries_fresh(cached, slx_dir):
            model = dict(cached)
            cache_hits[name] = True
        else:
            try:
                raw = session.call("picodesk_extract", str(slx))
                model = _normalize_matlab_json(json.loads(raw))
                model["rate_group"] = rate_group_or_none(model["base_rate_s"])
                _hash_dictionaries(model, slx_dir, model.pop("dictionaries"))
                _check_interface_catalog(model, model.pop("interface_catalog"))
            except (MatlabCallError, FileNotFoundError,
                    json.JSONDecodeError, KeyError) as exc:
                errors[name] = str(exc)
                continue
            cache_hits[name] = False

        model["file"] = slx.name
        model["slx_sha256"] = slx_hash
        check_fast_loop_types(name, model)  # enforced on hits too
        if not cache_hits[name]:
            cache.put(slx_hash, model)
        models[name] = model

    descriptor = {"schema_version": SCHEMA_VERSION, "models": models}
    validate_descriptor(descriptor)
    return descriptor, cache_hits, errors
