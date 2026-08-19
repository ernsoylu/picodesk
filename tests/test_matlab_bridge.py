"""Phase 4 tests: extraction pipeline, hash gating, MAT-002, crash recovery.

Everything here runs without MATLAB: the engine sits behind the session seam
and the tests drive a fake. Real-engine validation (session survives a forced
MATLAB kill, fixture .slx extraction) is a Phase 4 exit criterion that needs
a machine with MATLAB R2023b-R2024b installed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from picodesk.matlab_bridge import descriptor as d
from picodesk.matlab_bridge.extractor import (
    ExtractionCache,
    FloatInFastLoopError,
    extract_models,
    hash_slx,
)
from picodesk.matlab_bridge.session import (
    MatlabEngineSession,
    MatlabSessionError,
    check_python_alignment,
)

# --- fixtures ---------------------------------------------------------------

MOTOR = {
    "base_rate_s": 0.001,
    "inports": [
        {"name": "adc_phase_u", "data_type": "uint16", "width": 1},
        {"name": "derate_in", "data_type": "uint8", "width": 1},
    ],
    "outports": [
        {"name": "torque_cmd", "data_type": "int16", "width": 1,
         "slope": 0.0001, "bias": 0.0},
    ],
    "internal_types": ["int16", "int32"],
}

THERMAL = {
    "base_rate_s": 0.1,
    "inports": [{"name": "load_in", "data_type": "int16", "width": 1}],
    "outports": [{"name": "derate_pct", "data_type": "uint8", "width": 1}],
    "internal_types": ["double", "int32"],  # allowed: slow loop
}

TORQUE_ARB_BAD = {
    "base_rate_s": 0.001,
    "inports": [{"name": "trq_a", "data_type": "double", "width": 1}],
    "outports": [{"name": "trq_out", "data_type": "int16", "width": 1}],
    "internal_types": ["double"],
}


class FakeSession:
    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def call(self, function: str, slx_path: str) -> str:
        assert function == "picodesk_extract"
        self.calls.append(slx_path)
        return json.dumps(self.responses[Path(slx_path).stem])


def make_workspace(tmp_path: Path, models: dict[str, bytes]) -> Path:
    ws = tmp_path / "models"
    ws.mkdir()
    for name, content in models.items():
        (ws / f"{name}.slx").write_bytes(content)
    return ws


# --- hashing / gating (MAT-001, GUI-001, NFR-2) -----------------------------

def test_hash_tracks_content(tmp_path: Path) -> None:
    f = tmp_path / "m.slx"
    f.write_bytes(b"one")
    h1 = hash_slx(f)
    assert h1 == hash_slx(f)
    f.write_bytes(b"two")
    assert hash_slx(f) != h1


def test_fresh_extraction_builds_valid_descriptor(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, {"MotorCtrl": b"blob-a", "ThermalModel": b"blob-b"})
    session = FakeSession({"MotorCtrl": MOTOR, "ThermalModel": THERMAL})
    cache = ExtractionCache(tmp_path / "cache.json")

    desc, hits, errors = extract_models(session, ws, cache)

    assert errors == {}
    assert hits == {"MotorCtrl": False, "ThermalModel": False}
    assert len(session.calls) == 2
    assert desc["models"]["MotorCtrl"]["rate_group"] == "fast_1ms"
    assert desc["models"]["ThermalModel"]["rate_group"] == "slow_100ms"
    d.validate_descriptor(desc)  # schema holds


def test_rescan_makes_zero_matlab_calls(tmp_path: Path) -> None:
    """NFR-2: unchanged models cost no engine round trips."""
    ws = make_workspace(tmp_path, {"MotorCtrl": b"blob-a"})
    cache_file = tmp_path / "cache.json"
    s1 = FakeSession({"MotorCtrl": MOTOR})
    cache = ExtractionCache(cache_file)
    extract_models(s1, ws, cache)
    cache.save()

    s2 = FakeSession({})  # any call would KeyError
    desc, hits, _ = extract_models(s2, ws, ExtractionCache(cache_file))
    assert s2.calls == []
    assert hits == {"MotorCtrl": True}
    assert desc["models"]["MotorCtrl"]["inports"][0]["name"] == "adc_phase_u"


def test_content_change_forces_reextraction(tmp_path: Path) -> None:
    """GUI-001: a changed hash invalidates the cached descriptor."""
    ws = make_workspace(tmp_path, {"MotorCtrl": b"blob-a"})
    cache = ExtractionCache(tmp_path / "cache.json")
    extract_models(FakeSession({"MotorCtrl": MOTOR}), ws, cache)

    (ws / "MotorCtrl.slx").write_bytes(b"blob-CHANGED")
    session = FakeSession({"MotorCtrl": MOTOR})
    _, hits, _ = extract_models(session, ws, cache)
    assert hits == {"MotorCtrl": False}
    assert len(session.calls) == 1


def test_descriptor_dump_is_deterministic(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, {"MotorCtrl": b"a", "ThermalModel": b"b"})
    session = FakeSession({"MotorCtrl": MOTOR, "ThermalModel": THERMAL})
    desc1, _, _ = extract_models(session, ws, ExtractionCache(tmp_path / "c1.json"))
    desc2, _, _ = extract_models(session, ws, ExtractionCache(tmp_path / "c2.json"))
    assert d.dump_canonical(desc1) == d.dump_canonical(desc2)


# --- MAT-002 ----------------------------------------------------------------

def test_float_in_fast_loop_is_hard_error(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, {"TorqueArb": b"bad"})
    with pytest.raises(FloatInFastLoopError) as exc:
        extract_models(FakeSession({"TorqueArb": TORQUE_ARB_BAD}), ws,
                       ExtractionCache(tmp_path / "cache.json"))
    message = str(exc.value)
    assert "TorqueArb" in message
    assert "MAT-002" in message
    assert "trq_a" in message  # points at the offending port


def test_float_in_slow_loop_is_allowed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, {"ThermalModel": b"ok"})
    desc, _, _ = extract_models(FakeSession({"ThermalModel": THERMAL}), ws,
                                ExtractionCache(tmp_path / "cache.json"))
    assert "double" in desc["models"]["ThermalModel"]["internal_types"]


def test_unmatched_base_rate_is_rejected() -> None:
    with pytest.raises(ValueError, match="RTE-002"):
        d.rate_group_for(0.0042)


# --- rate-agnostic models + integration-time assignment (G-2) ---------------

RATE_AGNOSTIC = {
    # MATLAB invents 0.2 s for a fully inherited FixedStepAuto model.
    "base_rate_s": 0.2,
    "inports": [{"name": "temp_in", "data_type": "single", "width": 1}],
    "outports": [{"name": "led_out", "data_type": "boolean", "width": 1}],
    "internal_types": ["single", "boolean"],
}


def test_rate_agnostic_model_is_recorded_not_fatal(tmp_path: Path) -> None:
    """The interface-first development cycle leaves rates to integration;
    an unmatched compiled rate must mark the model, never abort the batch."""
    ws = make_workspace(tmp_path, {"Thermal2": b"x", "MotorCtrl": b"y"})
    session = FakeSession({"Thermal2": RATE_AGNOSTIC, "MotorCtrl": MOTOR})
    desc, _hits, errors = extract_models(
        session, ws, ExtractionCache(tmp_path / "cache.json"))
    assert errors == {}
    assert desc["models"]["Thermal2"]["rate_group"] is None
    assert desc["models"]["MotorCtrl"]["rate_group"] == "fast_1ms"
    d.validate_descriptor(desc)


def test_assignment_gives_the_group_and_runs_mat002(tmp_path: Path) -> None:
    from picodesk.matlab_bridge.extractor import apply_rate_assignments

    ws = make_workspace(tmp_path, {"Thermal2": b"x"})
    desc, _, _ = extract_models(FakeSession({"Thermal2": RATE_AGNOSTIC}), ws,
                                ExtractionCache(tmp_path / "c.json"))

    slow = apply_rate_assignments(desc, {"Thermal2": "slow_100ms"})
    assert slow["models"]["Thermal2"]["rate_group"] == "slow_100ms"
    assert desc["models"]["Thermal2"]["rate_group"] is None  # input untouched

    # The same model assigned fast is exactly as hard an error as a
    # modelled 1 ms float (MAT-002).
    with pytest.raises(FloatInFastLoopError, match="Thermal2"):
        apply_rate_assignments(desc, {"Thermal2": "fast_1ms"})


def test_assignment_cannot_override_a_modelled_rate(tmp_path: Path) -> None:
    from picodesk.matlab_bridge.extractor import apply_rate_assignments

    ws = make_workspace(tmp_path, {"MotorCtrl": b"y"})
    desc, _, _ = extract_models(FakeSession({"MotorCtrl": MOTOR}), ws,
                                ExtractionCache(tmp_path / "c.json"))
    with pytest.raises(ValueError, match="model's own rate wins"):
        apply_rate_assignments(desc, {"MotorCtrl": "slow_100ms"})
    with pytest.raises(ValueError, match="unknown model"):
        apply_rate_assignments(desc, {"Ghost": "slow_100ms"})


# --- dictionary closure in the hash gate (GUI-001, G-4) ---------------------

def make_dict_workspace(tmp_path: Path) -> tuple[Path, dict]:
    """A model attached to a shared dictionary chain, as MATLAB reports it."""
    ws = make_workspace(tmp_path, {"Ctl": b"slx-bytes"})
    (ws / "Ctl.sldd").write_bytes(b"dict-own")
    (ws / "Interfaces.sldd").write_bytes(b"dict-shared")
    reported = dict(MOTOR)
    reported["dictionaries"] = [str(ws / "Ctl.sldd"),
                                str(ws / "Interfaces.sldd")]
    reported["interface_catalog"] = []
    return ws, {"Ctl": reported}


def test_dictionary_closure_is_hashed_into_the_descriptor(tmp_path: Path) -> None:
    ws, responses = make_dict_workspace(tmp_path)
    desc, _, errors = extract_models(FakeSession(responses), ws,
                                     ExtractionCache(tmp_path / "c.json"))
    assert errors == {}
    entries = desc["models"]["Ctl"]["dictionaries"]
    assert [e["file"] for e in entries] == ["Ctl.sldd", "Interfaces.sldd"]
    assert entries[0]["sha256"] == hash_slx(ws / "Ctl.sldd")
    d.validate_descriptor(desc)


def test_dictionary_edit_invalidates_the_cache(tmp_path: Path) -> None:
    """G-4: editing a shared dictionary must be a cache MISS even though the
    .slx bytes are unchanged — the exact hole GUI-001 exists to close."""
    ws, responses = make_dict_workspace(tmp_path)
    cache_file = tmp_path / "cache.json"
    cache = ExtractionCache(cache_file)
    extract_models(FakeSession(responses), ws, cache)
    cache.save()

    # Unchanged everything -> hit, zero calls (NFR-2 still holds).
    s_hit = FakeSession(responses)
    _, hits, _ = extract_models(s_hit, ws, ExtractionCache(cache_file))
    assert hits == {"Ctl": True} and s_hit.calls == []

    # Dictionary edit, same .slx -> miss, re-extracted.
    (ws / "Interfaces.sldd").write_bytes(b"dict-shared-EDITED")
    s_miss = FakeSession(responses)
    _, hits, _ = extract_models(s_miss, ws, ExtractionCache(cache_file))
    assert hits == {"Ctl": False} and len(s_miss.calls) == 1


def test_missing_dictionary_is_a_per_model_error(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, {"Ctl": b"slx"})
    reported = dict(MOTOR)
    reported["dictionaries"] = ["Nowhere.sldd"]
    reported["interface_catalog"] = []
    desc, _, errors = extract_models(FakeSession({"Ctl": reported}), ws,
                                     ExtractionCache(tmp_path / "c.json"))
    assert "Ctl" not in desc["models"]
    assert "Nowhere.sldd" in errors["Ctl"]


# --- interface catalogue diagnostics (G-7 / G-8) ----------------------------

def test_port_contradicting_the_catalogue_is_diagnosed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, {"Ctl": b"slx"})
    reported = dict(RATE_AGNOSTIC)
    reported["dictionaries"] = []
    reported["interface_catalog"] = [
        {"name": "led_out", "dictionary": "Interfaces.sldd",
         "class": "Simulink.Signal", "data_type": "single"},   # model: boolean
        {"name": "temp_in", "dictionary": "Interfaces.sldd",
         "class": "Simulink.Signal", "data_type": "single"},   # matches
        {"name": "Thres_T", "dictionary": "Ctl.sldd",
         "class": "Simulink.Parameter", "data_type": "single"},
    ]
    desc, _, errors = extract_models(FakeSession({"Ctl": reported}), ws,
                                     ExtractionCache(tmp_path / "c.json"))
    assert errors == {}
    model = desc["models"]["Ctl"]
    assert model["interface_violations"] == [
        "outport led_out compiles as boolean but Interfaces.sldd declares single"]
    assert model["dictionary_parameters"] == [
        {"name": "Thres_T", "data_type": "single", "dictionary": "Ctl.sldd"}]
    d.validate_descriptor(desc)


# --- per-model errors keep the batch alive (G-9) ----------------------------

def test_broken_model_is_diagnosed_and_the_batch_continues(tmp_path: Path) -> None:
    from picodesk.matlab_bridge.session import MatlabCallError

    class MixedSession(FakeSession):
        def call(self, function: str, slx_path: str) -> str:
            if "Broken" in slx_path:
                raise MatlabCallError("Unable to find data dictionary 'x.sldd'.")
            return super().call(function, slx_path)

    ws = make_workspace(tmp_path, {"Broken": b"bad", "MotorCtrl": b"good"})
    desc, batch_hits, errors = extract_models(
        MixedSession({"MotorCtrl": MOTOR}), ws,
        ExtractionCache(tmp_path / "cache.json"))
    assert "MotorCtrl" in desc["models"]
    assert "Broken" not in desc["models"]
    assert "x.sldd" in errors["Broken"]
    assert batch_hits == {"MotorCtrl": False}


# --- schema migrations ------------------------------------------------------

def test_descriptor_v1_migrates_and_future_versions_refuse() -> None:
    v1 = {"schema_version": 1, "models": {}}
    assert d.migrate_descriptor(v1)["schema_version"] == d.SCHEMA_VERSION
    with pytest.raises(ValueError, match="newer"):
        d.migrate_descriptor({"schema_version": 99, "models": {}})


# --- session crash recovery (GUI-002) ---------------------------------------

class MatlabExecutionError(Exception):
    """Same NAME as matlab.engine's class: the session detects execution
    errors by class name so matlabengine stays an optional import."""


class ExecutionErrorEngine:
    """Engine that is perfectly healthy but whose MATLAB code raises."""

    def __init__(self) -> None:
        self.calls = 0

    def feval(self, name: str, *args, nargout: int = 1):
        if name == "addpath":
            return
        self.calls += 1
        raise MatlabExecutionError("Unable to find data dictionary 'm.sldd'.")

    def quit(self) -> None:
        pass


def test_execution_error_does_not_restart_the_engine() -> None:
    """G-9: a MATLAB-side error means the engine is alive; restarting it
    costs a cold start and used to abort every remaining model in a batch."""
    from picodesk.matlab_bridge.session import MatlabCallError

    engines: list[ExecutionErrorEngine] = []

    def factory() -> ExecutionErrorEngine:
        engines.append(ExecutionErrorEngine())
        return engines[-1]

    session = MatlabEngineSession(engine_factory=factory)
    with pytest.raises(MatlabCallError, match="m.sldd"):
        session.call("picodesk_extract", "x.slx")
    assert len(engines) == 1, "healthy engine was restarted"
    assert engines[0].calls == 1, "failed call was retried"
    assert session.alive


class FlakyEngine:
    """Dies on the first N feval calls, then behaves."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def feval(self, name: str, *args, nargout: int = 1):
        if name == "addpath":
            return None
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("engine terminated")
        return "ok"

    def quit(self) -> None:
        pass


def test_session_recovers_from_one_crash() -> None:
    engines: list[FlakyEngine] = []

    def factory() -> FlakyEngine:
        engines.append(FlakyEngine(failures=1 if not engines else 0))
        return engines[-1]

    session = MatlabEngineSession(engine_factory=factory)
    assert session.call("picodesk_extract", "x.slx") == "ok"
    assert len(engines) == 2  # crashed engine replaced transparently


def test_session_gives_up_after_second_failure() -> None:
    session = MatlabEngineSession(engine_factory=lambda: FlakyEngine(failures=99))
    with pytest.raises(MatlabSessionError, match="failed twice"):
        session.call("picodesk_extract", "x.slx")


# --- version alignment (SRS section 8) --------------------------------------

def test_python_alignment_known_release() -> None:
    # This suite runs on 3.9-3.11 in CI, which every supported release allows.
    import sys

    problem = check_python_alignment("R2024a")
    if sys.version_info[:2] in ((3, 9), (3, 10), (3, 11)):
        assert problem is None
    else:
        assert problem is not None


def test_python_alignment_unknown_release() -> None:
    assert "R2019b" in (check_python_alignment("R2019b") or "")
