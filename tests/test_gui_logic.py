"""Phase 7 tests for the Qt-free half of the GUI.

Every rule the interface enforces lives in a view-model, so the rules are
tested directly here and the widget layer stays a thin rendering of them.
No QApplication is needed for anything in this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from picodesk.gui import workspace as ws
from picodesk.gui.console import ConsoleModel
from picodesk.gui.pages.calibration_page import CalibrationSession
from picodesk.gui.pages.models_page import model_state
from picodesk.gui.routing_model import Connection, Port, RoutingModel
from picodesk.rtegen.routing import load_hal_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "gen_ws"
HAL_MANIFEST = Path(__file__).parent.parent / "target" / "hal" / "hal_manifest.json"


@pytest.fixture
def descriptor() -> dict:
    return json.loads((FIXTURES / "descriptor.json").read_text())


@pytest.fixture
def routing() -> dict:
    return json.loads((FIXTURES / "routing.json").read_text())


@pytest.fixture
def hal() -> dict:
    return load_hal_manifest(HAL_MANIFEST)


@pytest.fixture
def model(descriptor, routing, hal) -> RoutingModel:
    return RoutingModel.from_workspace(descriptor, routing, hal)


# --- workspace versioning (GUI-003) -----------------------------------------

def test_current_schema_loads_without_migration(tmp_path) -> None:
    path = tmp_path / "w.pdws"
    path.write_text(json.dumps({
        "schema_version": ws.WORKSPACE_SCHEMA_VERSION,
        "model_dir": "/models", "descriptor": {}, "routing": {}}))
    assert not ws.needs_migration(path)
    assert ws.load(path).model_dir == "/models"


def test_old_schema_refuses_to_load_without_consent(tmp_path) -> None:
    """GUI-003: migration is offered, never silently performed."""
    path = tmp_path / "old.pdws"
    path.write_text(json.dumps({"schema_version": 1, "connections": []}))
    assert ws.needs_migration(path)
    with pytest.raises(ws.WorkspaceError, match="needs migration"):
        ws.load(path)


def test_migration_chains_v1_to_current_and_keeps_backup(tmp_path) -> None:
    path = tmp_path / "old.pdws"
    path.write_text(json.dumps({
        "schema_version": 1, "model_dir": "/m",
        "connections": [{"producer": "A:y", "consumer": "B:u"}]}))

    workspace = ws.load(path, migrate=True)

    assert workspace.migrated_from == 1
    # v1->v2 moved connections under routing; v2->v3 switched the separator.
    assert workspace.routing["connections"] == [
        {"producer": "A.y", "consumer": "B.u"}]
    backup = tmp_path / "old.v1.pdws"
    assert backup.is_file(), "original must be preserved"
    assert json.loads(backup.read_text())["schema_version"] == 1
    # The migration is persisted, so the next open needs no migration.
    assert not ws.needs_migration(path)


def test_future_schema_is_refused(tmp_path) -> None:
    path = tmp_path / "new.pdws"
    path.write_text(json.dumps(
        {"schema_version": ws.WORKSPACE_SCHEMA_VERSION + 5}))
    with pytest.raises(ws.WorkspaceError, match="newer PicoDesk"):
        ws.load(path, migrate=True)


def test_non_workspace_file_is_rejected(tmp_path) -> None:
    path = tmp_path / "junk.pdws"
    path.write_text('{"hello": 1}')
    with pytest.raises(ws.WorkspaceError, match="not a workspace"):
        ws.peek_version(path)


# --- routing rules (GUI-008 … GUI-011) --------------------------------------

def test_type_filter_blocks_mismatches(model) -> None:
    """GUI-008: only exact type/width/scaling matches are selectable."""
    rows = {port.ref: (ok, why) for port, ok, why
            in model.selectable_consumers("FastCtrl.torque_cmd")}
    # int16 producer -> uint8 consumer must be rejected, with the reason.
    ok, why = rows["hal.hal_gpio_write"]
    assert not ok
    assert "int16" in why and "uint8" in why
    # An already-bound inport reports that instead — the more useful reason.
    ok, why = rows["FastCtrl.derate_in"]
    assert not ok
    assert "already bound" in why


def test_scaling_difference_blocks_binding() -> None:
    producer = Port("A", "y", "int16", slope=0.001)
    consumer = Port("B", "u", "int16", slope=1.0)
    ok, why = producer.compatible_with(consumer)
    assert not ok
    assert "scaling" in why


def test_bound_inport_is_locked_to_one_writer(model) -> None:
    """GUI-009: a second producer cannot claim a bound inport."""
    assert model.writer_of("FastCtrl.adc_u") == "hal.hal_adc_read"
    with pytest.raises(ValueError, match="already bound"):
        model.connect("hal.hal_gpio_read", "FastCtrl.adc_u")


def test_cascade_delete_unlocks_dependents(model) -> None:
    """GUI-009: removing a model unlinks every edge that touched it."""
    assert model.is_bound("FastCtrl.derate_in")
    removed = model.remove_model("SlowSense")
    assert len(removed) == 2  # one inbound, one outbound
    assert not model.is_bound("FastCtrl.derate_in")
    assert "SlowSense" not in model.model_names()


def test_rate_transition_classification(model) -> None:
    """GUI-010: cross-group edges are ZOH/seqlock, same-group are direct."""
    cross = Connection("FastCtrl.torque_cmd", "SlowSense.load_in")
    assert model.mechanism(cross) == "zoh_seqlock"
    assert "RATE TRANSITION" in model.badge(cross)

    hal_edge = Connection("hal.hal_adc_read", "FastCtrl.adc_u")
    assert model.mechanism(hal_edge) == "direct"
    assert model.badge(hal_edge) == "DIRECT · IN-ISR"


def test_non_isr_safe_hal_blocked_on_fast_model(descriptor, routing) -> None:
    """GUI-006 surfaced in the matrix, not just at build time."""
    hal = {"hal_slow_read": {"name": "hal_slow_read", "peripheral": "X",
                             "direction": "producer", "data_type": "uint16",
                             "isr_safe": False}}
    model = RoutingModel.from_workspace(
        descriptor, {"schema_version": 1, "connections": []}, hal)
    rows = {port.ref: (ok, why) for port, ok, why
            in model.selectable_consumers("hal.hal_slow_read")}
    ok, why = rows["FastCtrl.adc_u"]
    assert not ok
    assert "ISR-safe" in why


def test_suggestions_are_exact_and_unambiguous(descriptor) -> None:
    """GUI-011: exact name+type matches only, ambiguity excluded."""
    descriptor["models"]["Echo"] = {
        "file": "Echo.slx", "slx_sha256": "f" * 64, "base_rate_s": 0.001,
        "rate_group": "fast_1ms",
        "inports": [{"name": "torque_cmd", "data_type": "int16", "width": 1}],
        "outports": [], "internal_types": ["int16"],
    }
    model = RoutingModel.from_workspace(
        descriptor, {"schema_version": 1, "connections": []}, {})
    suggestions = model.suggest_bindings()
    pairs = {(c.producer, c.consumer) for c, _ in suggestions}
    assert ("FastCtrl.torque_cmd", "Echo.torque_cmd") in pairs

    # A second producer of the same name makes it ambiguous -> dropped.
    descriptor["models"]["Rival"] = dict(
        descriptor["models"]["FastCtrl"],
        inports=[], outports=[{"name": "torque_cmd", "data_type": "int16",
                               "width": 1}])
    ambiguous = RoutingModel.from_workspace(
        descriptor, {"schema_version": 1, "connections": []}, {})
    assert not any(c.consumer == "Echo.torque_cmd"
                   for c, _ in ambiguous.suggest_bindings())


def test_suggestions_apply_and_undo_atomically(descriptor) -> None:
    descriptor["models"]["Echo"] = {
        "file": "Echo.slx", "slx_sha256": "f" * 64, "base_rate_s": 0.001,
        "rate_group": "fast_1ms",
        "inports": [{"name": "torque_cmd", "data_type": "int16", "width": 1}],
        "outports": [], "internal_types": ["int16"],
    }
    model = RoutingModel.from_workspace(
        descriptor, {"schema_version": 1, "connections": []}, {})
    before = len(model.connections)
    applied = model.apply_suggestions(c for c, _ in model.suggest_bindings())
    assert applied and len(model.connections) == before + len(applied)
    assert model.undo_suggestions(applied) == len(applied)
    assert len(model.connections) == before


def test_routing_round_trips_through_config(model) -> None:
    rebuilt = RoutingModel(producers=model.producers,
                           consumers=model.consumers)
    rebuilt.connections = [Connection(**{k: v for k, v in c.items()})
                           for c in model.to_routing()["connections"]]
    assert rebuilt.to_routing() == model.to_routing()


def test_stats_report_full_mesh_shape(model) -> None:
    stats = model.stats()
    assert stats["total"] == 3
    assert stats["model_to_model"] == 2   # ASW <-> ASW both directions
    assert stats["hal"] == 1
    assert stats["cross_rate"] == 2


# --- models page state (GUI-001, MAT-002) -----------------------------------

def test_model_state_prioritises_mat002_over_staleness(descriptor) -> None:
    fast = descriptor["models"]["FastCtrl"]
    assert model_state("FastCtrl", fast, stale=False)[0] == "Fresh"
    assert model_state("FastCtrl", fast, stale=True)[0].startswith("Stale")

    fast["internal_types"].append("double")
    # A blocked model is blocked whether or not it is also stale.
    assert model_state("FastCtrl", fast, stale=True)[0] == "Blocked · MAT-002"


def test_slow_model_may_use_float(descriptor) -> None:
    slow = descriptor["models"]["SlowSense"]
    slow["internal_types"].append("double")
    assert model_state("SlowSense", slow, stale=False)[0] == "Fresh"


# --- console (GUI-005) ------------------------------------------------------

def test_console_counts_and_filters() -> None:
    console = ConsoleModel()
    console.append("[cmake] [1/2] Building")
    console.append("SRC/a.c:10:3: warning: unused variable 'x'")
    console.append("SRC/b.c:22:1: error: undefined reference")

    assert console.counts() == {"errors": 1, "warnings": 1, "lines": 3}
    assert len(console.filtered("Errors")) == 1
    assert len(console.filtered("Warnings+")) == 2
    assert len(console.filtered("All")) == 3

    diagnostic = console.lines[2][1]
    assert diagnostic is not None
    assert (diagnostic.file, diagnostic.line) == ("SRC/b.c", 22)


def test_console_is_bounded() -> None:
    console = ConsoleModel(max_lines=10)
    for i in range(50):
        console.append(f"line {i}")
    assert console.counts()["lines"] == 10
    assert console.lines[-1][0] == "line 49"


# --- calibration session (RTE-003 semantics) --------------------------------

def test_edits_stay_offline_until_the_page_switch() -> None:
    session = CalibrationSession(active_values={"kp": 9830.0, "ki": 655.0})
    session.edit("kp", 11469.0)

    assert session.pending == 1
    assert session.active_values["kp"] == 9830.0       # live value untouched
    assert session.display_value("kp") == (11469.0, True)
    assert session.display_value("ki") == (655.0, False)

    assert session.request_switch()
    assert session.switch_pending
    assert session.active_values["kp"] == 9830.0       # still not committed

    session.commit()                                    # target reports swap
    assert session.active_values["kp"] == 11469.0
    assert session.pending == 0
    assert not session.switch_pending
    assert session.active_page == 1


def test_switch_refused_with_nothing_pending() -> None:
    session = CalibrationSession(active_values={"kp": 1.0})
    assert not session.request_switch()
    assert not session.switch_pending


def test_editing_back_to_the_active_value_clears_the_change() -> None:
    session = CalibrationSession(active_values={"kp": 9830.0})
    session.edit("kp", 11469.0)
    session.edit("kp", 9830.0)
    assert session.pending == 0


def test_discard_drops_offline_edits() -> None:
    session = CalibrationSession(active_values={"kp": 9830.0})
    session.edit("kp", 1.0)
    session.discard()
    assert session.pending == 0
    assert session.active_values["kp"] == 9830.0


def test_unknown_parameter_is_rejected() -> None:
    session = CalibrationSession(active_values={"kp": 1.0})
    with pytest.raises(KeyError):
        session.edit("nope", 2.0)
