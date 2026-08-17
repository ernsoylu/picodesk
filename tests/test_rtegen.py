"""Phase 5 tests: routing validation, edge classification, code generation."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from picodesk.rtegen.generator import generate_rte
from picodesk.rtegen.routing import (
    RoutingError,
    load_hal_manifest,
    resolve_routing,
)

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


# --- validation rules -------------------------------------------------------

def test_valid_routing_classifies_edges(descriptor, routing, hal) -> None:
    edges = resolve_routing(descriptor, routing, hal)
    mechanisms = {(e.producer.owner, e.consumer.owner): e.mechanism for e in edges}
    assert mechanisms[("hal_adc_read", "FastCtrl")] == "direct"
    assert mechanisms[("FastCtrl", "SlowSense")] == "zoh_seqlock"  # GUI-010
    assert mechanisms[("SlowSense", "FastCtrl")] == "zoh_seqlock"


def test_single_writer_enforced(descriptor, routing, hal) -> None:
    routing["connections"].append(
        {"producer": "SlowSense.derate_pct", "consumer": "FastCtrl.adc_u"})
    routing["connections"].append(
        {"producer": "FastCtrl.torque_cmd", "consumer": "FastCtrl.adc_u"})
    with pytest.raises(RoutingError, match="GUI-009"):
        resolve_routing(descriptor, routing, hal)


def test_type_mismatch_rejected(descriptor, routing, hal) -> None:
    routing["connections"] = [
        {"producer": "FastCtrl.torque_cmd", "consumer": "FastCtrl.derate_in"}]
    with pytest.raises(RoutingError, match="GUI-008"):
        resolve_routing(descriptor, routing, hal)


def test_scaling_mismatch_rejected(descriptor, routing, hal) -> None:
    descriptor["models"]["FastCtrl"]["outports"][0]["slope"] = 0.0001
    routing["connections"] = [
        {"producer": "FastCtrl.torque_cmd", "consumer": "SlowSense.load_in"}]
    with pytest.raises(RoutingError, match="GUI-008"):
        resolve_routing(descriptor, routing, hal)


def test_non_isr_safe_hal_rejected_on_fast_model(descriptor, routing, hal) -> None:
    hal = copy.deepcopy(hal)
    hal["hal_adc_read"]["isr_safe"] = False
    with pytest.raises(RoutingError, match="GUI-006"):
        resolve_routing(descriptor, routing, hal)


def test_unknown_endpoints_rejected(descriptor, routing, hal) -> None:
    for bad in ("Nope.port", "FastCtrl.nope", "hal.nope"):
        broken = copy.deepcopy(routing)
        broken["connections"] = [
            {"producer": bad, "consumer": "SlowSense.load_in"}]
        with pytest.raises(RoutingError):
            resolve_routing(descriptor, broken, hal)


def test_hal_to_hal_rejected(descriptor, routing, hal) -> None:
    routing["connections"] = [
        {"producer": "hal.hal_adc_read", "consumer": "hal.hal_pwm_set_duty"}]
    with pytest.raises(RoutingError, match="not routable"):
        resolve_routing(descriptor, routing, hal)


# --- generation -------------------------------------------------------------

def test_generation_emits_expected_sources(descriptor, routing, hal,
                                           tmp_path) -> None:
    written = generate_rte(descriptor, routing, hal, tmp_path)
    names = {p.name for p in written}
    assert names == {"rte_gen.h", "rte_gen.c", "pd_FastCtrl_io.h",
                     "pd_SlowSense_io.h"}

    c = (tmp_path / "rte_gen.c").read_text()
    # Fast path pinned to SRAM (BLD-001).
    assert "__not_in_flash_func(rte_gen_fast_isr)" in c
    # Bi-directional cross-rate buses (RTE-004) with reader shadows.
    assert "bus_fast_1ms__to__slow_100ms" in c
    assert "bus_slow_100ms__to__fast_1ms" in c
    assert c.count("rte_seqlock_write") == 2
    assert c.count("rte_seqlock_read") == 2
    # HAL read wired into the fast copy-in (GUI-006).
    assert "hal_adc_read(0)" in c
    # Rate-monotonic priorities + 2 kB stacks (BLD-005).
    assert "RTE_GEN_TASK_STACK_WORDS 512" in c

    h = (tmp_path / "rte_gen.h").read_text()
    assert "rte_gen_daq_frame_t" in h  # RTE-005 frame


def test_generation_is_deterministic(descriptor, routing, hal, tmp_path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    generate_rte(descriptor, routing, hal, a)
    generate_rte(descriptor, routing, hal, b)
    for f in sorted(a.iterdir()):
        assert f.read_bytes() == (b / f.name).read_bytes(), f.name


def test_symbol_prefix_collision_detected(descriptor, routing, hal,
                                          tmp_path) -> None:
    descriptor["models"]["fastctrl"] = copy.deepcopy(
        descriptor["models"]["FastCtrl"])
    with pytest.raises(RoutingError, match="MAT-003"):
        generate_rte(descriptor, routing, hal, tmp_path)


def test_seqlock_payload_bound_enforced(descriptor, routing, hal,
                                        tmp_path) -> None:
    """RTE-004: a cross-rate bus larger than the seqlock bound is rejected."""
    fast = descriptor["models"]["FastCtrl"]
    slow = descriptor["models"]["SlowSense"]
    for i in range(20):
        fast["outports"].append(
            {"name": f"sig{i}", "data_type": "int32", "width": 1})
        slow["inports"].append(
            {"name": f"sig{i}_in", "data_type": "int32", "width": 1})
        routing["connections"].append(
            {"producer": f"FastCtrl.sig{i}", "consumer": f"SlowSense.sig{i}_in"})
    with pytest.raises(RoutingError, match="RTE-004"):
        generate_rte(descriptor, routing, hal, tmp_path)


def test_28_model_synthetic_workspace_generates(hal, tmp_path) -> None:
    """M1 scale check: a full-size workspace generates collision-free."""
    models: dict = {}
    connections = []
    groups = ["fast_1ms", "slow_10ms", "slow_100ms"]
    rates = {"fast_1ms": 0.001, "slow_10ms": 0.01, "slow_100ms": 0.1}
    for i in range(28):
        group = groups[i % 3]
        models[f"Model{i:02d}"] = {
            "file": f"Model{i:02d}.slx",
            "slx_sha256": "c" * 64,
            "base_rate_s": rates[group],
            "rate_group": group,
            "inports": [{"name": "u", "data_type": "int16", "width": 1}],
            "outports": [{"name": "y", "data_type": "int16", "width": 1}],
            "internal_types": ["int16"],
        }
    for i in range(27):
        connections.append({"producer": f"Model{i:02d}.y",
                            "consumer": f"Model{i + 1:02d}.u"})
    descriptor = {"schema_version": 1, "models": models}
    routing = {"schema_version": 1, "connections": connections}

    written = generate_rte(descriptor, routing, hal, tmp_path)
    assert len(written) == 2 + 28
    c = (tmp_path / "rte_gen.c").read_text()
    for i in range(28):
        assert f"pd_Model{i:02d}_step" in c
