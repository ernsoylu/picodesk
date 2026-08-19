"""Phase 4 exit criteria against a LIVE MATLAB engine.

Gated behind PICODESK_MATLAB_LIVE=1 (CI has no MATLAB and skips this file).
Covers what the fake-engine suite cannot:
  - real .slx fixtures built programmatically, extracted through
    picodesk_extract.m with compiled port types
  - MAT-002 hard error from a real double-typed fast-loop model
  - hash gating with a real engine (zero engine calls on re-scan, NFR-2)
  - session survives a SIGKILLed MATLAB process (GUI-002)

R2025b is the pinned release (SRS section 8, amended in v7.1).
"""

from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest
from picodesk.matlab_bridge.extractor import (
    ExtractionCache,
    FloatInFastLoopError,
    extract_models,
)
from picodesk.matlab_bridge.session import MatlabEngineSession, check_python_alignment

pytestmark = pytest.mark.skipif(
    os.environ.get("PICODESK_MATLAB_LIVE") != "1",
    reason="live MATLAB suite: set PICODESK_MATLAB_LIVE=1 on a machine with MATLAB",
)

FIXTURE_DIR = Path(__file__).parent


class SpySession:
    """Counts extraction calls to prove the NFR-2 gate with a real engine."""

    def __init__(self, inner: MatlabEngineSession) -> None:
        self.inner = inner
        self.extract_calls = 0

    def call(self, function: str, *args, **kwargs):
        if function == "picodesk_extract":
            self.extract_calls += 1
        return self.inner.call(function, *args, **kwargs)


@pytest.fixture(scope="module")
def session():
    s = MatlabEngineSession()
    s.start()
    s.call("addpath", str(FIXTURE_DIR), nargout=0)
    yield s
    s.stop()


@pytest.fixture(scope="module")
def workspace(session, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("slx_fixtures")
    session.call("make_fixture_models", str(out), nargout=0)
    assert (out / "good" / "FastCtrl.slx").is_file()
    assert (out / "bad" / "TorqueArbBad.slx").is_file()
    return out


def test_real_extraction_of_reference_models(session, workspace, tmp_path) -> None:
    spy = SpySession(session)
    cache = ExtractionCache(tmp_path / "cache.json")
    descriptor, hits, errors = extract_models(spy, workspace / "good", cache)
    assert errors == {}

    fast = descriptor["models"]["FastCtrl"]
    assert fast["rate_group"] == "fast_1ms"
    assert fast["base_rate_s"] == pytest.approx(0.001)
    in_types = {p["name"]: p["data_type"] for p in fast["inports"]}
    assert in_types == {"adc_u": "uint16", "derate_in": "uint8"}
    assert fast["outports"][0]["data_type"] == "int16"

    thermal = descriptor["models"]["ThermalModel"]
    assert thermal["rate_group"] == "slow_100ms"
    assert "double" in thermal["internal_types"]  # allowed in the slow loop

    assert hits == {"FastCtrl": False, "ThermalModel": False}
    assert spy.extract_calls == 2


def test_mat002_hard_error_from_real_model(session, workspace, tmp_path) -> None:
    with pytest.raises(FloatInFastLoopError) as exc:
        extract_models(session, workspace / "bad",
                       ExtractionCache(tmp_path / "cache.json"))
    assert "TorqueArbBad" in str(exc.value)
    assert "MAT-002" in str(exc.value)


def test_rescan_costs_zero_engine_calls(session, workspace, tmp_path) -> None:
    """NFR-2 with the real engine, not a fake."""
    cache_file = tmp_path / "cache.json"
    cache = ExtractionCache(cache_file)
    extract_models(SpySession(session), workspace / "good", cache)
    cache.save()

    spy = SpySession(session)
    _, hits, _ = extract_models(spy, workspace / "good", ExtractionCache(cache_file))
    assert spy.extract_calls == 0
    assert all(hits.values())


EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "models"

examples_required = pytest.mark.skipif(
    not EXAMPLES.is_dir(), reason="examples/models workspace not present")


@examples_required
def test_examples_workspace_extracts_end_to_end(session, tmp_path) -> None:
    """The reference workspace (interface-first, shared data dictionaries,
    rate-agnostic models) extracts without a single failure — G-1/G-2/G-7 as
    a living regression, not a one-off investigation."""
    cache = ExtractionCache(tmp_path / "cache.json")
    desc, _hits, errors = extract_models(session, EXAMPLES, cache)
    assert errors == {}
    assert sorted(desc["models"]) == ["model1", "model2", "model3"]

    for model in desc["models"].values():
        # Rate-agnostic by design: assigned at integration time (G-2).
        assert model["rate_group"] is None
        # The dictionary closure is recorded with hashes (G-4), including
        # the shared Interfaces.sldd, relative to the workspace.
        files = [d["file"] for d in model["dictionaries"]]
        assert "Interfaces.sldd" in files
        assert all(not f.startswith("/") for f in files)

    # model3 contradicts the declared interface; diagnosed at the source (G-7).
    assert desc["models"]["model3"]["interface_violations"] == [
        ("outport GPO_Led1_B compiles as single but Interfaces.sldd "
         "declares boolean")]
    assert "interface_violations" not in desc["models"]["model1"]
    # The dictionary calibratable is surfaced for the G-8 diagnostic.
    assert desc["models"]["model2"]["dictionary_parameters"] == [
        {"name": "Thres_T", "data_type": "single", "dictionary": "model2.sldd"}]


@examples_required
def test_shared_dictionary_edit_invalidates_every_attached_model(
        session, tmp_path) -> None:
    """G-4 against real dictionaries: touching Interfaces.sldd misses the
    cache for all three models even though no .slx changed."""
    cache_file = tmp_path / "cache.json"
    cache = ExtractionCache(cache_file)
    extract_models(session, EXAMPLES, cache)
    cache.save()

    _, rescan_hits, _ = extract_models(session, EXAMPLES,
                                       ExtractionCache(cache_file))
    assert all(rescan_hits.values()), "unchanged workspace must be all hits"

    shared = EXAMPLES / "Interfaces.sldd"
    original = shared.read_bytes()
    shared.write_bytes(original + b" ")
    try:
        _, hits, errors = extract_models(session, EXAMPLES,
                                         ExtractionCache(cache_file))
        assert errors == {}
        assert not any(hits.values()), "dictionary edit must invalidate"
    finally:
        shared.write_bytes(original)


@examples_required
def test_codegen_forces_prefixed_identifiers_and_the_assigned_rate(
        session, tmp_path) -> None:
    """G-3: the Coder advisor's rt$N$M naming (rtU/ExtU, unprefixed — the
    shipped model1_ert_rtw is the evidence) is overridden at codegen so
    thirty models can link (MAT-003); the assigned dispatcher rate is forced
    at the same time (G-2)."""
    session.call("picodesk_codegen", str(EXAMPLES / "model1.slx"),
                 str(tmp_path), 0.01)
    code = (tmp_path / "model1_ert_rtw" / "model1.c").read_text()
    header = (tmp_path / "model1_ert_rtw" / "model1.h").read_text()
    assert "model1_U;" in code and "model1_Y;" in code
    assert "ExtU_model1_T" in header
    assert "rtU" not in code, "advisor naming leaked through"
    assert "void model1_step(void)" in code


def test_session_survives_engine_kill(session) -> None:
    """GUI-002: SIGKILL the MATLAB process; the next call must recover."""
    pid = int(session.call("feature", "getpid"))
    os.kill(pid, signal.SIGKILL)
    assert float(session.call("plus", 20.0, 22.0)) == 42.0
    new_pid = int(session.call("feature", "getpid"))
    assert new_pid != pid


def test_pinned_release_is_accepted() -> None:
    """R2025b is the pinned release (SRS v7.1): the checker must accept it
    on any Python in its supported range."""
    import sys

    from picodesk.matlab_bridge.session import PINNED_RELEASE

    assert PINNED_RELEASE == "R2025b"
    problem = check_python_alignment(PINNED_RELEASE)
    if sys.version_info[:2] in ((3, 9), (3, 10), (3, 11), (3, 12)):
        assert problem is None, problem
    else:
        assert problem is not None


def test_unknown_release_is_still_flagged() -> None:
    assert "R2019b" in (check_python_alignment("R2019b") or "")


def test_ert_codegen_produces_step_code(session, workspace, tmp_path) -> None:
    """Real Embedded Coder run on the FastCtrl fixture (MAT-003 groundwork):
    ERT emits <model>_step with no software-float in the generated C."""
    session.call("cd", str(tmp_path), nargout=0)
    session.call("load_system", str(workspace / "good" / "FastCtrl.slx"),
                 nargout=0)
    try:
        # GenCodeOnly (not GenerateCodeOnly) is the Embedded Coder parameter
        # name; verified against the installed release.
        session.call("set_param", "FastCtrl",
                     "SystemTargetFile", "ert.tlc",
                     "GenCodeOnly", "on", nargout=0)
        session.call("slbuild", "FastCtrl", nargout=0)
    finally:
        session.call("close_system", "FastCtrl", 0.0, nargout=0)

    gen_c = tmp_path / "FastCtrl_ert_rtw" / "FastCtrl.c"
    assert gen_c.is_file(), "ERT did not produce FastCtrl.c"
    code = gen_c.read_text(encoding="utf-8", errors="replace")
    assert "FastCtrl_step" in code
    # Fixed-point fixture: the generated math must be integer-only (MAT-002).
    # ERT's float types are real_T/real32_T; neither may appear in the step
    # code of an all-integer model.
    assert "real_T" not in code
    assert "real32_T" not in code
