"""Live Tuning panel driven end to end without a board (GUI-012).

The panel's backends used to be `NotImplementedError` stubs and its signals
were connected to nothing, so the widget tests passed while pressing Connect
did nothing at all. These tests exist so that cannot recur: they drive the real
`MainWindow` handlers against the same `FakeSlave` the master tests use, so the
whole chain — button, signal, handler, master, slave, page update — is covered.

Only the serial leg is out of scope, and it is the O-4 hardware gate.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from picodesk.gui import theme
from picodesk.gui.main_window import MainWindow
from picodesk.xcp.master import DaqSignal, Parameter
from PyQt6.QtWidgets import QApplication
from test_xcp_master import CAL_BASE, FRAME_BASE, FakeSlave

PARAMS = [
    Parameter("kp_q15", CAL_BASE + 0, "i32"),
    Parameter("ki_q15", CAL_BASE + 4, "i32"),
    Parameter("trq_limit", CAL_BASE + 8, "i32"),
]
SIGNALS = [
    DaqSignal("torque_cmd", FRAME_BASE + 4, "i32"),
    DaqSignal("speed_est", FRAME_BASE + 12, "i32"),
]


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyleSheet(theme.STYLESHEET)
    yield application


@pytest.fixture
def window(app, qtbot=None):
    slave = FakeSlave()
    win = MainWindow(xcp_backend_factory=lambda port: slave)
    win.xcp_parameters = list(PARAMS)
    win.xcp_daq_signals = list(SIGNALS)
    win.slave = slave  # test handle
    yield win
    win.close()


def console_text(window) -> str:
    """The console keeps its lines in a Qt-free model (see gui/console.py)."""
    return "\n".join(line for line, _ in window.console.model.lines)


def connect(window) -> None:
    """Run the connect worker synchronously — the pool is an implementation
    detail, and the behaviour under test is the handler chain."""
    window.toggle_xcp_link()
    window.pool.waitForDone(5000)
    QApplication.processEvents()


# --- session --------------------------------------------------------------


def test_connect_populates_the_panel(window) -> None:
    assert window.xcp is None
    connect(window)

    assert window.xcp is not None and window.xcp.connected
    assert window.slave.connected
    assert window.calibration_page.session.active_values == {
        "kp_q15": 9830, "ki_q15": 655, "trq_limit": 32767}
    assert window.calibration_page.connect_button.text() == "Disconnect"
    assert [s.name for s in SIGNALS] == window.calibration_page.selected_signals()


def test_connect_failure_reports_and_leaves_the_panel_usable(app) -> None:
    """No board attached is the normal case off the bench. It must surface in
    the console, not raise, and must not leave a half-connected session."""
    class DeadBackend:
        def connect(self):
            raise OSError("could not open /dev/ttyACM0")

    win = MainWindow(xcp_backend_factory=lambda port: DeadBackend())
    try:
        win.toggle_xcp_link()
        win.pool.waitForDone(5000)
        QApplication.processEvents()
        assert win.xcp is None
        assert win.calibration_page.connect_button.text() == "Connect"
        assert "ttyACM0" in console_text(win)
    finally:
        win.close()


def test_disconnect_returns_to_the_initial_state(window) -> None:
    connect(window)
    window.toggle_xcp_link()  # second press disconnects
    assert window.xcp is None
    assert not window.slave.connected
    assert window.calibration_page.connect_button.text() == "Connect"


# --- calibration is transactional through the whole chain (RTE-003) -------


def test_edit_then_switch_goes_live_only_after_the_target_commits(window) -> None:
    connect(window)
    page = window.calibration_page

    page.session.edit("kp_q15", 11469)
    assert page.session.pending == 1
    assert window.slave.live("kp_q15") == 9830

    # The target commits on its own schedule; do it as soon as it is armed so
    # the handler's poll loop observes the switch.
    original_set = window.slave.set_cal_page

    def arm_then_commit(segment, page_no):
        original_set(segment, page_no)
        window.slave.commit()

    window.slave.set_cal_page = arm_then_commit

    window.apply_calibration()
    window.pool.waitForDone(5000)
    QApplication.processEvents()

    assert window.slave.live("kp_q15") == 11469
    assert page.session.pending == 0, "panel still shows the edit as pending"
    assert page.session.active_values["kp_q15"] == 11469


def test_apply_without_a_connection_is_reported_not_crashed(window) -> None:
    window.calibration_page.session.active_values = {"kp_q15": 1}
    window.calibration_page.session.edit("kp_q15", 2)
    window.apply_calibration()
    assert "not connected" in console_text(window)


def test_apply_with_no_edits_does_nothing(window) -> None:
    connect(window)
    before = window.slave.live("kp_q15")
    window.apply_calibration()
    window.pool.waitForDone(5000)
    assert window.slave.live("kp_q15") == before


# --- recording (GUI-012) --------------------------------------------------


def test_record_writes_an_mdf4_from_the_daq_stream(window, tmp_path: Path) -> None:
    from asammdf import MDF

    connect(window)
    window.recording_path = lambda: tmp_path / "run.mf4"

    window.toggle_recording(True)
    assert window.recorder.recording
    assert window.slave.daq_running

    for i in range(10):
        window.slave.push_frame(i * 1000, -100 - i, 4000 + i)
    window._drain_daq()
    assert window.recorder.sample_count == 10

    window.toggle_recording(False)
    assert not window.slave.daq_running
    out = tmp_path / "run.mf4"
    assert out.is_file(), "no MDF4 written"
    with MDF(out) as mdf:
        assert list(mdf.get("torque_cmd").samples[:3]) == [-100, -101, -102]
        assert list(mdf.get("speed_est").samples[:3]) == [4000, 4001, 4002]
    assert str(out) in console_text(window)


def test_record_without_a_connection_is_refused(window) -> None:
    window.toggle_recording(True)
    assert not window.recorder.recording
    assert "nothing to record" in console_text(window)


def test_record_with_no_signals_selected_is_refused(window) -> None:
    connect(window)
    for i in range(window.calibration_page.signal_list.count()):
        item = window.calibration_page.signal_list.item(i)
        item.setCheckState(item.checkState().Unchecked)
    window.toggle_recording(True)
    assert not window.recorder.recording
    assert "select at least one" in console_text(window)


def test_disconnect_while_recording_saves_the_file(window, tmp_path: Path) -> None:
    """Pulling the link must not lose what was already captured."""
    connect(window)
    window.recording_path = lambda: tmp_path / "partial.mf4"
    window.toggle_recording(True)
    window.slave.push_frame(0, 1, 2)
    window._drain_daq()

    window.disconnect_xcp()
    assert (tmp_path / "partial.mf4").is_file()
    assert not window.recorder.recording
