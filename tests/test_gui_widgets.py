"""Phase 7 widget tests — run headless via QT_QPA_PLATFORM=offscreen.

These check that the widgets actually render the state the view-models
describe (a rule enforced in the model but not shown in the UI would still
be a defect), and that the build gate really disables its button.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from picodesk.buildsys import sizing_report as sz
from picodesk.buildsys.dependency_checker import DependencyStatus
from picodesk.gui import theme
from picodesk.gui.console import DiagnosticConsole
from picodesk.gui.pages.build_page import BuildPage
from picodesk.gui.pages.calibration_page import CalibrationPage
from picodesk.gui.pages.models_page import ModelsPage
from picodesk.gui.pages.routing_page import RoutingPage
from picodesk.rtegen.routing import load_hal_manifest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

FIXTURES = Path(__file__).parent / "fixtures" / "gen_ws"
HAL_MANIFEST = Path(__file__).parent.parent / "target" / "hal" / "hal_manifest.json"


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyleSheet(theme.STYLESHEET)
    yield application


@pytest.fixture
def descriptor() -> dict:
    return json.loads((FIXTURES / "descriptor.json").read_text())


@pytest.fixture
def routing() -> dict:
    return json.loads((FIXTURES / "routing.json").read_text())


# --- models page ------------------------------------------------------------

def test_models_table_renders_states(app, descriptor) -> None:
    page = ModelsPage()
    descriptor["models"]["Bad"] = {
        "file": "Bad.slx", "slx_sha256": "a" * 64, "base_rate_s": 0.001,
        "rate_group": "fast_1ms",
        "inports": [{"name": "u", "data_type": "double", "width": 1}],
        "outports": [], "internal_types": ["double"],
    }
    page.set_descriptor(descriptor, stale={"SlowSense"})

    assert page.table.topLevelItemCount() == 3
    assert page.blocked_models() == ["Bad"]
    assert "1 stale" in page.subtitle.text()
    assert "1 blocked" in page.subtitle.text()
    # The state cell is a widget; the item text must stay empty or the two
    # draw on top of each other.
    for row in range(page.table.topLevelItemCount()):
        assert page.table.topLevelItem(row).text(6) == ""
        assert page.table.itemWidget(page.table.topLevelItem(row), 2) is not None


def test_selecting_a_model_shows_its_ports(app, descriptor) -> None:
    page = ModelsPage()
    page.set_descriptor(descriptor)
    page.table.setCurrentItem(page.table.topLevelItem(0))
    rendered = [page.detail_body.itemAt(i).widget().text()
                for i in range(page.detail_body.count())
                if page.detail_body.itemAt(i).widget() is not None
                and hasattr(page.detail_body.itemAt(i).widget(), "text")]
    assert any("FastCtrl" in text for text in rendered)
    assert any("OUTPORTS" in text for text in rendered)


# --- routing page -----------------------------------------------------------

def test_routing_panes_reflect_the_model(app, descriptor, routing) -> None:
    page = RoutingPage()
    page.load(descriptor, routing, load_hal_manifest(HAL_MANIFEST))

    assert page.producer_list.count() == len(page.model.producers)
    assert page.connection_list.count() == 3
    assert "cross-rate" in page.subtitle.text()

    # Bound inports show the lock and their writer (GUI-009).
    locked = [page.consumer_list.item(i).text()
              for i in range(page.consumer_list.count())
              if "🔒" in page.consumer_list.item(i).text()]
    assert any("hal.hal_adc_read" in text for text in locked)


def test_selecting_producer_disables_incompatible_rows(app, descriptor,
                                                       routing) -> None:
    """GUI-008 must be visible, not merely enforced underneath."""
    page = RoutingPage()
    page.load(descriptor, routing, load_hal_manifest(HAL_MANIFEST))
    for i in range(page.producer_list.count()):
        item = page.producer_list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == "FastCtrl.torque_cmd":
            page.producer_list.setCurrentItem(item)
            break

    states = {page.consumer_list.item(i).data(Qt.ItemDataRole.UserRole):
              bool(page.consumer_list.item(i).flags()
                   & Qt.ItemFlag.ItemIsEnabled)
              for i in range(page.consumer_list.count())}
    assert states["FastCtrl.derate_in"] is False   # uint8 vs int16
    assert states["FastCtrl.adc_u"] is False       # already bound


def test_double_click_binds_and_unlinks(app, descriptor) -> None:
    page = RoutingPage()
    page.load(descriptor, {"schema_version": 1, "connections": []}, {})
    for i in range(page.producer_list.count()):
        item = page.producer_list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == "FastCtrl.torque_cmd":
            page.producer_list.setCurrentItem(item)
            break
    target = next(page.consumer_list.item(i)
                  for i in range(page.consumer_list.count())
                  if page.consumer_list.item(i).data(Qt.ItemDataRole.UserRole)
                  == "SlowSense.load_in")

    page._connect_selected(target)
    assert page.model.writer_of("SlowSense.load_in") == "FastCtrl.torque_cmd"
    assert page.connection_list.count() == 1

    page._unlink_selected(page.connection_list.item(0))
    assert page.model.writer_of("SlowSense.load_in") is None


# --- build page -------------------------------------------------------------

def _deps(gcc_ok: bool = True) -> list[DependencyStatus]:
    return [
        DependencyStatus("arm-none-eabi-gcc", True, gcc_ok, "12.2.1", gcc_ok),
        DependencyStatus("cmake", True, True, "3.28", True),
        DependencyStatus("pico-sdk", True, True, "1.5.1", True),
        DependencyStatus("matlab", False, False, "", False),
    ]


def test_build_blocked_without_toolchain(app) -> None:
    page = BuildPage()
    page.set_dependencies(_deps(gcc_ok=False))
    assert not page.build_button.isEnabled()
    assert any("GUI-004" in r for r in page.gate_reasons())


def test_build_blocked_by_mat002_and_by_sizing(app, descriptor,
                                               routing) -> None:
    page = BuildPage()
    page.set_dependencies(_deps())
    page.set_sizing(sz.estimate_footprint(descriptor, routing))
    assert page.build_button.isEnabled(), "a fitting workspace must be buildable"

    page.set_blocked_models(["TorqueArb"])
    assert not page.build_button.isEnabled()
    assert any("MAT-002" in r for r in page.gate_reasons())

    page.set_blocked_models([])
    oversized = sz.SizingReport(sram_total=sz.SRAM_LIMIT_BYTES * 2,
                                flash_total=1024,
                                banks={"SRAM0": 1024})
    page.set_sizing(oversized)
    assert not page.build_button.isEnabled()
    assert any("BLD-004" in r for r in page.gate_reasons())


def test_reproducibility_chip_states(app) -> None:
    page = BuildPage()
    page.set_reproducible(True, "7c1d90aa5f2b")
    assert "Reproducible" in page.repro_chip.label.text()
    page.set_reproducible(False, "")
    assert "differs" in page.repro_chip.label.text()


# --- calibration page -------------------------------------------------------

def test_calibration_banner_tracks_pending_edits(app) -> None:
    page = CalibrationPage()
    page.set_parameters({"kp_q15": 9830.0, "ki_q15": 655.0})
    assert not page.switch_button.isEnabled()
    assert "No pending changes" in page.banner_label.text()

    page.session.edit("kp_q15", 11469.0)
    page._update_banner()
    assert page.switch_button.isEnabled()
    assert "1 pending change" in page.banner_label.text()
    assert "invisible to the running loop" in page.banner_label.text()

    page._request_switch()
    assert "model_step boundary" in page.banner_label.text()
    assert not page.switch_button.isEnabled()  # no double-arming

    page.notify_switch_committed()
    assert "No pending changes" in page.banner_label.text()
    assert page.session.active_values["kp_q15"] == 11469.0


def test_telemetry_tiles_colour_by_health(app) -> None:
    page = CalibrationPage()
    page.update_telemetry({"exec_max_us": 612, "overrun_count": 0,
                           "seqlock_fault_count": 0, "daq_rate_hz": 1000,
                           "heartbeat_delta": 1000})
    assert page.tiles["isr"].value.text() == "61.2"
    assert page.tiles["watchdog"].value.text() == "OK"

    page.update_telemetry({"overrun_count": 3, "heartbeat_delta": 0})
    assert page.tiles["overrun"].value.text() == "3"
    assert theme.ERR in page.tiles["overrun"].value.styleSheet()
    assert page.tiles["watchdog"].value.text() == "STALLED"


def test_signal_selection(app) -> None:
    page = CalibrationPage()
    page.set_signals(["a.x", "b.y"], {"a.x": "fast_1ms"})
    assert page.selected_signals() == ["a.x", "b.y"]
    page.signal_list.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert page.selected_signals() == ["a.x"]


# --- console widget ---------------------------------------------------------

def test_console_widget_renders_and_links(app) -> None:
    console = DiagnosticConsole()
    console.append_line("[cmake] building")
    console.append_line("SRC/x.c:5:1: error: boom")
    assert "1 errors" in console.summary.text()
    assert "SRC/x.c:5" in console.view.toHtml()
    # GUI-005: the diagnostic must be a real, clickable link.
    assert "picodesk://open?file=" in console.view.toHtml()

    received = []
    console.locationActivated.connect(
        lambda path, line: received.append((path, line)))
    from PyQt6.QtCore import QUrl
    console._on_anchor(QUrl("picodesk://open?file=SRC%2Fx.c&line=5"))
    # Case must survive the round trip — QUrl lowercases host components,
    # so the path travels in the query instead.
    assert received == [("SRC/x.c", 5)]
    console.clear()
    assert console.model.counts()["lines"] == 0


# --- integration-time rate assignment (G-2) ---------------------------------

def test_unassigned_model_blocks_build_until_a_rate_is_chosen(app) -> None:
    """A rate-agnostic model (interface-first development cycle) surfaces as
    blocked with an assignment combo; choosing a group persists into the
    routing config (an integration decision) and unblocks the model."""
    from picodesk.gui.main_window import MainWindow

    win = MainWindow()
    try:
        win.workspace.descriptor = {
            "schema_version": 2,
            "models": {
                "ThermalX": {
                    "file": "ThermalX.slx", "slx_sha256": "0" * 64,
                    "base_rate_s": 0.2, "rate_group": None,
                    "inports": [{"name": "t_in", "data_type": "single",
                                 "width": 1}],
                    "outports": [{"name": "led", "data_type": "boolean",
                                  "width": 1}],
                    "internal_types": ["single"],
                },
            },
        }
        win._apply_workspace()
        assert win.models_page.blocked_models() == ["ThermalX"]
        combo = win.models_page.table.itemWidget(
            win.models_page.table.topLevelItem(0), 2)
        assert combo is not None and combo.currentData() is None

        # MAT-002 on assignment: single-carrying model refused for fast.
        win.assign_rate_group("ThermalX", "fast_1ms")
        assert win.workspace.routing.get("rate_assignments", {}) == {}

        win.assign_rate_group("ThermalX", "slow_100ms")
        assert win.workspace.routing["rate_assignments"] == {
            "ThermalX": "slow_100ms"}
        assert win.models_page.blocked_models() == []
        combo = win.models_page.table.itemWidget(
            win.models_page.table.topLevelItem(0), 2)
        assert combo.currentData() == "slow_100ms"  # stays editable
    finally:
        win.close()


def test_interface_violation_is_surfaced_not_hidden(app) -> None:
    """G-7: a model contradicting the dictionary-declared interface shows a
    warning state at the source, before any confusing bind error."""
    from picodesk.gui.pages.models_page import model_state

    model = {
        "base_rate_s": 0.1, "rate_group": "slow_100ms",
        "inports": [], "outports": [
            {"name": "GPO_Led1_B", "data_type": "single", "width": 1}],
        "internal_types": ["single"],
        "interface_violations": [
            ("outport GPO_Led1_B compiles as single but Interfaces.sldd "
             "declares boolean")],
    }
    state, _colour = model_state("model3", model, stale=False)
    assert state == "Interface mismatch"
