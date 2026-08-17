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

    desc, hits = extract_models(session, ws, cache)

    assert hits == {"MotorCtrl": False, "ThermalModel": False}
    assert len(session.calls) == 2
    assert desc["models"]["MotorCtrl"]["rate_group"] == "fast_1ms"
    assert desc["models"]["ThermalModel"]["rate_group"] == "slow_100ms"
    d.validate_descriptor(desc)  # schema v1 holds


def test_rescan_makes_zero_matlab_calls(tmp_path: Path) -> None:
    """NFR-2: unchanged models cost no engine round trips."""
    ws = make_workspace(tmp_path, {"MotorCtrl": b"blob-a"})
    cache_file = tmp_path / "cache.json"
    s1 = FakeSession({"MotorCtrl": MOTOR})
    cache = ExtractionCache(cache_file)
    extract_models(s1, ws, cache)
    cache.save()

    s2 = FakeSession({})  # any call would KeyError
    desc, hits = extract_models(s2, ws, ExtractionCache(cache_file))
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
    _, hits = extract_models(session, ws, cache)
    assert hits == {"MotorCtrl": False}
    assert len(session.calls) == 1


def test_descriptor_dump_is_deterministic(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, {"MotorCtrl": b"a", "ThermalModel": b"b"})
    session = FakeSession({"MotorCtrl": MOTOR, "ThermalModel": THERMAL})
    desc1, _ = extract_models(session, ws, ExtractionCache(tmp_path / "c1.json"))
    desc2, _ = extract_models(session, ws, ExtractionCache(tmp_path / "c2.json"))
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
    desc, _ = extract_models(FakeSession({"ThermalModel": THERMAL}), ws,
                             ExtractionCache(tmp_path / "cache.json"))
    assert "double" in desc["models"]["ThermalModel"]["internal_types"]


def test_unmatched_base_rate_is_rejected() -> None:
    with pytest.raises(ValueError, match="RTE-002"):
        d.rate_group_for(0.0042)


# --- session crash recovery (GUI-002) ---------------------------------------

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
