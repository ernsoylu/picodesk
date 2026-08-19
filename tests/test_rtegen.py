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


def test_hal_writer_identity_is_per_pin(descriptor, routing, hal) -> None:
    """G-5: hal_gpio_write on two different pins is two endpoints — the
    sample workspace drives two LEDs through the same HAL function. The same
    pin twice stays a genuine double-drive."""
    descriptor["models"]["SlowSense"]["outports"].append(
        {"name": "led_b", "data_type": "uint8", "width": 1,
         "slope": 1.0, "bias": 0.0})
    routing["connections"] = [
        {"producer": "SlowSense.derate_pct", "consumer": "hal.hal_gpio_write",
         "hal_arg": 24},
        {"producer": "SlowSense.led_b", "consumer": "hal.hal_gpio_write",
         "hal_arg": 25},
    ]
    edges = resolve_routing(descriptor, routing, hal)  # two pins: legal
    assert [e.consumer.hal_arg for e in edges] == [24, 25]

    routing["connections"][1]["hal_arg"] = 24  # same pin: double-drive
    with pytest.raises(RoutingError, match="GUI-009"):
        resolve_routing(descriptor, routing, hal)


def test_unassigned_rate_group_blocks_routing(descriptor, routing, hal) -> None:
    """G-2: a rate-agnostic model cannot be routed until a group is
    assigned; the error names the remedy."""
    descriptor["models"]["SlowSense"]["rate_group"] = None
    with pytest.raises(RoutingError, match="rate_assignments"):
        resolve_routing(descriptor, routing, hal)


def test_rate_assignment_flows_through_routing_and_generation(
        descriptor, routing, hal, tmp_path: Path) -> None:
    """G-2 end to end: the assignment classifies edges AND reaches the
    generated dispatcher tables."""
    descriptor["models"]["SlowSense"]["rate_group"] = None
    routing["rate_assignments"] = {"SlowSense": "slow_100ms"}

    edges = resolve_routing(descriptor, routing, hal)
    mechanisms = {(e.producer.owner, e.consumer.owner): e.mechanism
                  for e in edges}
    assert mechanisms[("FastCtrl", "SlowSense")] == "zoh_seqlock"

    written = generate_rte(descriptor, routing, hal, tmp_path)
    rte_c = (tmp_path / "rte_gen.c").read_text()
    assert "slow_100ms" in rte_c
    assert descriptor["models"]["SlowSense"]["rate_group"] is None, \
        "generate_rte mutated the caller's descriptor"
    assert written


def test_assigning_float_model_to_fast_fails_before_codegen(
        descriptor, routing, hal) -> None:
    """MAT-002 re-runs at assignment time (G-2)."""
    descriptor["models"]["SlowSense"]["rate_group"] = None
    descriptor["models"]["SlowSense"]["internal_types"] = ["double"]
    routing["rate_assignments"] = {"SlowSense": "fast_1ms"}
    from picodesk.matlab_bridge.extractor import FloatInFastLoopError
    with pytest.raises(FloatInFastLoopError, match="MAT-002"):
        resolve_routing(descriptor, routing, hal)


def test_routing_v1_config_migrates_transparently(descriptor, routing, hal) -> None:
    """GUI-003: the fixture is schema v1; v2 code accepts it via migration
    and a future version is refused rather than guessed at."""
    assert routing["schema_version"] == 1
    assert resolve_routing(descriptor, routing, hal)
    from picodesk.rtegen.routing import migrate_routing
    with pytest.raises(RoutingError, match="newer"):
        migrate_routing({"schema_version": 99, "connections": []})


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


# --- ERT adapters (O-9) -----------------------------------------------------

def test_ert_adapters_split_by_rate_group(descriptor, tmp_path) -> None:
    """The fast/slow directory split is load-bearing: the linker script
    places models/fast/* in SRAM (BLD-001)."""
    from picodesk.rtegen.generator import generate_ert_adapters

    written = generate_ert_adapters(descriptor, tmp_path)
    by_name = {p.name: p for p in written}
    assert by_name["pd_FastCtrl_ert.c"].parent.name == "fast"
    assert by_name["pd_SlowSense_ert.c"].parent.name == "slow"


def test_ert_adapter_is_the_copy_in_boundary(descriptor, tmp_path) -> None:
    """RTE-001: the adapter copies inputs in, steps, copies outputs out —
    and touches the ERT globals nowhere else."""
    from picodesk.rtegen.generator import generate_ert_adapters

    generate_ert_adapters(descriptor, tmp_path, models=["FastCtrl"])
    text = (tmp_path / "fast" / "pd_FastCtrl_ert.c").read_text()

    assert "FastCtrl_U.adc_u = in->adc_u;" in text
    assert "FastCtrl_U.derate_in = in->derate_in;" in text
    assert "FastCtrl_step();" in text
    assert "out->torque_cmd = FastCtrl_Y.torque_cmd;" in text
    # Fast-loop adapters must carry the SRAM-resident definition macro.
    assert "PD_FASTCTRL_STEP" in text
    # A comment must never contain a C comment terminator: an earlier
    # version documented the linker glob inline and broke the file.
    body = text.split("*/", 1)[1]
    assert "#include" in body, "block comment terminated early"


# --- array signals (O-10) ---------------------------------------------------

@pytest.fixture
def array_workspace() -> tuple[dict, dict]:
    """A cross-rate array signal plus a scalar, so both paths are covered."""
    descriptor = {
        "schema_version": 1,
        "models": {
            "Sensor": {
                "file": "Sensor.slx", "slx_sha256": "a" * 64,
                "base_rate_s": 0.001, "rate_group": "fast_1ms",
                "inports": [],
                "outports": [
                    {"name": "chan", "data_type": "int16", "width": 4},
                    {"name": "status", "data_type": "uint8", "width": 1},
                ],
                "internal_types": ["int16"],
            },
            "Filter": {
                "file": "Filter.slx", "slx_sha256": "b" * 64,
                "base_rate_s": 0.1, "rate_group": "slow_100ms",
                "inports": [
                    {"name": "chan_in", "data_type": "int16", "width": 4},
                    {"name": "status_in", "data_type": "uint8", "width": 1},
                ],
                "outports": [],
                "internal_types": ["int16"],
            },
        },
    }
    routing = {"schema_version": 1, "connections": [
        {"producer": "Sensor.chan", "consumer": "Filter.chan_in"},
        {"producer": "Sensor.status", "consumer": "Filter.status_in"},
    ]}
    return descriptor, routing


def test_array_signals_route(array_workspace, hal) -> None:
    descriptor, routing = array_workspace
    edges = resolve_routing(descriptor, routing, hal)
    assert len(edges) == 2
    assert all(e.mechanism == "zoh_seqlock" for e in edges)


def test_array_width_mismatch_still_rejected(array_workspace, hal) -> None:
    """GUI-008 keeps demanding an exact width match."""
    descriptor, routing = array_workspace
    descriptor["models"]["Filter"]["inports"][0]["width"] = 8
    with pytest.raises(RoutingError, match="width 4 != 8"):
        resolve_routing(descriptor, routing, hal)


def test_array_signals_generate_memcpy(array_workspace, hal, tmp_path) -> None:
    """Arrays cannot be assigned or placed in a designated initializer, so
    every copy of one must go through memcpy."""
    descriptor, routing = array_workspace
    generate_rte(descriptor, routing, hal, tmp_path)
    c = (tmp_path / "rte_gen.c").read_text()

    # Store, bus field and DAQ frame all carry the dimension.
    assert "int16_t Sensor_chan[4];" in c
    assert "memcpy(tmp.Sensor_chan, s_store_fast_1ms.Sensor_chan" in c
    assert "memcpy(frame.Sensor_chan, s_store_fast_1ms.Sensor_chan" in c
    # The scalar beside it still uses plain assignment.
    assert "tmp.Sensor_status = s_store_fast_1ms.Sensor_status;" in c
    # Consumer copy-in from the seqlock shadow.
    assert "memcpy(in.chan_in, s_shadow_bus" in c

    h = (tmp_path / "rte_gen.h").read_text()
    assert "int16_t Sensor_chan[4];" in h
    io = (tmp_path / "pd_Sensor_io.h").read_text()
    assert "int16_t chan[4];" in io


def test_array_bus_counts_toward_the_seqlock_bound(hal, tmp_path) -> None:
    """RTE-004: width multiplies the payload, so the bound must see it."""
    descriptor = {
        "schema_version": 1,
        "models": {
            "Wide": {
                "file": "Wide.slx", "slx_sha256": "c" * 64,
                "base_rate_s": 0.001, "rate_group": "fast_1ms",
                "inports": [],
                "outports": [{"name": "big", "data_type": "int32",
                              "width": 32}],  # 128 bytes > 64-byte bound
                "internal_types": ["int32"],
            },
            "Sink": {
                "file": "Sink.slx", "slx_sha256": "d" * 64,
                "base_rate_s": 0.1, "rate_group": "slow_100ms",
                "inports": [{"name": "big_in", "data_type": "int32",
                             "width": 32}],
                "outports": [], "internal_types": ["int32"],
            },
        },
    }
    routing = {"schema_version": 1, "connections": [
        {"producer": "Wide.big", "consumer": "Sink.big_in"}]}
    with pytest.raises(RoutingError, match="RTE-004"):
        generate_rte(descriptor, routing, hal, tmp_path)
