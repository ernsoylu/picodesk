"""Versioned workspace file with safe migration (GUI-003).

A workspace bundles everything the GUI needs to rebuild a firmware: where
the models live, the extracted descriptor, and the routing config. It is
saved as versioned JSON; opening an older schema offers an automatic
migration that keeps a backup of the original rather than overwriting it.

Migrations are registered one per version step, so v1 -> v3 runs v1->v2 then
v2->v3 and no migration ever has to know about more than its own step.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

WORKSPACE_SCHEMA_VERSION = 3

MigrationFn = Callable[[dict], dict]
_MIGRATIONS: dict[int, MigrationFn] = {}


def migration(from_version: int) -> Callable[[MigrationFn], MigrationFn]:
    def register(fn: MigrationFn) -> MigrationFn:
        _MIGRATIONS[from_version] = fn
        return fn
    return register


class WorkspaceError(RuntimeError):
    """The file is not a usable workspace."""


@dataclass
class Workspace:
    path: Path | None = None
    model_dir: str = ""
    descriptor: dict[str, Any] = field(default_factory=dict)
    routing: dict[str, Any] = field(
        default_factory=lambda: {"schema_version": 1, "connections": []})
    migrated_from: int | None = None

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "model_dir": self.model_dir,
            "descriptor": self.descriptor,
            "routing": self.routing,
        }

    def save(self, path: Path | None = None) -> Path:
        target = path or self.path
        if target is None:
            raise WorkspaceError("no path to save to")
        target.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        self.path = target
        return target


def peek_version(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"{path}: not readable as JSON ({exc})") from exc
    version = data.get("schema_version")
    if not isinstance(version, int):
        raise WorkspaceError(f"{path}: no schema_version — not a workspace")
    return version


def needs_migration(path: Path) -> bool:
    return peek_version(path) < WORKSPACE_SCHEMA_VERSION


def backup_path(path: Path) -> Path:
    return path.with_suffix(f".v{peek_version(path)}{path.suffix}")


def load(path: Path, *, migrate: bool = False,
         keep_backup: bool = True) -> Workspace:
    """Load a workspace, optionally migrating it forward.

    Raises WorkspaceError when the file is newer than this build, or when it
    is older and `migrate` was not granted — the GUI asks first (GUI-003).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    version = peek_version(path)

    if version > WORKSPACE_SCHEMA_VERSION:
        raise WorkspaceError(
            f"{path}: schema v{version} was written by a newer PicoDesk "
            f"(this build understands v{WORKSPACE_SCHEMA_VERSION}); upgrade "
            f"rather than risk a lossy downgrade")

    migrated_from = None
    if version < WORKSPACE_SCHEMA_VERSION:
        if not migrate:
            raise WorkspaceError(
                f"{path}: schema v{version} needs migration to "
                f"v{WORKSPACE_SCHEMA_VERSION}")
        if keep_backup:
            shutil.copy2(path, backup_path(path))
        migrated_from = version
        data = migrate_data(data)

    workspace = Workspace(
        path=path,
        model_dir=data.get("model_dir", ""),
        descriptor=data.get("descriptor", {}),
        routing=data.get("routing", {"schema_version": 1, "connections": []}),
        migrated_from=migrated_from,
    )
    if migrated_from is not None:
        workspace.save()  # persist the migration result
    return workspace


def migrate_data(data: dict[str, Any]) -> dict[str, Any]:
    """Apply one registered migration per version step."""
    version = data["schema_version"]
    while version < WORKSPACE_SCHEMA_VERSION:
        step = _MIGRATIONS.get(version)
        if step is None:
            raise WorkspaceError(
                f"no migration registered from schema v{version}")
        data = step(dict(data))
        new_version = data["schema_version"]
        if new_version <= version:
            raise WorkspaceError(
                f"migration from v{version} did not advance the version")
        version = new_version
    return data


# --- registered migrations --------------------------------------------------

@migration(1)
def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """v1 stored connections as a bare list at the top level; v2 moved them
    into a versioned routing object."""
    connections = data.pop("connections", [])
    data["routing"] = {"schema_version": 1, "connections": connections}
    data["schema_version"] = 2
    return data


@migration(2)
def _v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """v2 named endpoints "model:port"; v3 uses "model.port" so HAL endpoints
    ("hal.hal_adc_read") and model endpoints share one grammar."""
    routing = data.get("routing") or {"schema_version": 1, "connections": []}
    for conn in routing.get("connections", []):
        for key in ("producer", "consumer"):
            value = conn.get(key)
            if isinstance(value, str) and ":" in value:
                conn[key] = value.replace(":", ".", 1)
    data["routing"] = routing
    data["schema_version"] = 3
    return data
