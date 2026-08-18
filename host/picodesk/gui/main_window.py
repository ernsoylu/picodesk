"""Application shell (7a): sidebar navigation, pages, diagnostic console.

Wires the four pages to the real backends built in Phases 4–6. Everything
slow — MATLAB extraction, RTE generation, CMake — goes through
`picodesk.gui.workers`, so the UI thread only ever handles signals; that is
what keeps the interaction budget (no stall over 100 ms) achievable.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from picodesk.buildsys import sizing_report as sz
from picodesk.buildsys.dependency_checker import check_dependencies
from picodesk.gui import theme
from picodesk.gui import workspace as ws
from picodesk.gui.console import DiagnosticConsole
from picodesk.gui.pages.build_page import BuildPage
from picodesk.gui.pages.calibration_page import CalibrationPage
from picodesk.gui.pages.models_page import ModelsPage
from picodesk.gui.pages.routing_page import RoutingPage
from picodesk.gui.widgets import StatusChip
from picodesk.gui.workers import submit
from picodesk.rtegen.routing import load_hal_manifest
from picodesk.xcp.master import DaqSignal, Parameter, PyxcpBackend, XcpMaster
from picodesk.xcp.mdf4_logger import Mdf4Logger, SignalSpec

REPO_ROOT = Path(__file__).resolve().parents[3]
HAL_MANIFEST = REPO_ROOT / "target" / "hal" / "hal_manifest.json"

# Live-tuning defaults. These describe the spike firmware, which is what a
# bring-up board runs; a workspace with its own A2L will supply its own, and
# both are overridable on the window so tests can drive the panel. They are
# deliberately NOT part of the workspace schema — adding fields there means a
# version bump and a migration (GUI-003), which is not worth spending on
# something the UI cannot configure yet.
DEFAULT_XCP_PORT = "/dev/ttyACM0"
DEFAULT_CAL_BASE = 0x2002_0000
DEFAULT_DAQ_FRAME_BASE = 0x2002_1000
DEFAULT_CAL_PARAMETERS = [
    Parameter("kp_q15", DEFAULT_CAL_BASE + 0, "i32"),
    Parameter("ki_q15", DEFAULT_CAL_BASE + 4, "i32"),
    Parameter("trq_limit", DEFAULT_CAL_BASE + 8, "i32"),
]
DEFAULT_DAQ_SIGNALS = [
    DaqSignal("tick", DEFAULT_DAQ_FRAME_BASE + 0, "u32"),
    DaqSignal("torque_cmd", DEFAULT_DAQ_FRAME_BASE + 4, "i32"),
    DaqSignal("iq_meas", DEFAULT_DAQ_FRAME_BASE + 8, "i32"),
    DaqSignal("speed_est", DEFAULT_DAQ_FRAME_BASE + 12, "i32"),
]

NAV = [
    ("Models", "models"),
    ("Routing", "routing"),
    ("Build", "build"),
    ("Calibration", "calibration"),
]


class Sidebar(QWidget):
    navigated = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(4)

        logo = QHBoxLayout()
        mark = QLabel("◆")
        mark.setStyleSheet(f"color:{theme.ACCENT}; font-size:16px;")
        logo.addWidget(mark)
        name = QLabel("PicoDesk")
        name.setObjectName("LogoText")
        logo.addWidget(name)
        version = QLabel("0.1")
        version.setObjectName("LogoVersion")
        logo.addWidget(version)
        logo.addStretch(1)
        layout.addLayout(logo)
        layout.addSpacing(14)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for index, (label, _key) in enumerate(NAV):
            button = QPushButton(label)
            button.setObjectName("NavItem")
            button.setCheckable(True)
            button.setChecked(index == 0)
            self.group.addButton(button, index)
            layout.addWidget(button)
        self.group.idClicked.connect(self.navigated)

        layout.addStretch(1)

        self.workspace_label = QLabel("no workspace")
        self.workspace_label.setObjectName("Muted")
        self.workspace_label.setStyleSheet(
            f"font-family:{theme.FONT_MONO_STACK}; font-size:10px;")
        self.workspace_label.setWordWrap(True)
        layout.addWidget(self.workspace_label)

        self.toolchain_chip = StatusChip("checking toolchain…", theme.TEXT_MUTED)
        self.toolchain_chip.set_elide_width(150)
        layout.addWidget(self.toolchain_chip)

        # GPL-3.0 asks an interactive program to carry an Appropriate Legal
        # Notice (section 5d): the licence and the absence of warranty, where
        # the user will see it. PicoDesk is GPL because PyQt6 is used under its
        # GPL option — see docs/THIRD_PARTY_LICENSES.md.
        licence = QLabel("GPL-3.0-or-later · no warranty · see LICENSE")
        licence.setObjectName("Muted")
        licence.setStyleSheet(f"font-size:9px; color:{theme.TEXT_MUTED};")
        licence.setWordWrap(True)
        layout.addWidget(licence)


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None, *,
                 xcp_backend_factory: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PicoDesk")
        self.resize(1440, 900)
        self.pool = QThreadPool.globalInstance()
        self.workspace = ws.Workspace()
        self.hal_manifest: dict[str, dict[str, Any]] = {}
        if HAL_MANIFEST.is_file():
            self.hal_manifest = load_hal_manifest(HAL_MANIFEST)

        # Injectable so the Live Tuning panel can be driven end to end without
        # a board (tests/test_gui_widgets.py). The default is the real serial
        # backend, whose one untestable leg is the O-4 hardware gate.
        self._xcp_backend_factory = xcp_backend_factory or (
            lambda port: PyxcpBackend(port))
        self.xcp: XcpMaster | None = None
        self.recorder = Mdf4Logger()
        self._daq_timer: QTimer | None = None
        self.xcp_port = DEFAULT_XCP_PORT
        self.xcp_parameters = list(DEFAULT_CAL_PARAMETERS)
        self.xcp_daq_signals = list(DEFAULT_DAQ_SIGNALS)

        self._build()
        self._check_dependencies()

    # -- construction -------------------------------------------------------

    def _build(self) -> None:
        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.navigated.connect(self._navigate)
        outer.addWidget(self.sidebar)

        right = QSplitter(Qt.Orientation.Vertical)
        self.pages = QStackedWidget()
        self.models_page = ModelsPage()
        self.routing_page = RoutingPage()
        self.build_page = BuildPage()
        self.calibration_page = CalibrationPage()
        for page in (self.models_page, self.routing_page, self.build_page,
                     self.calibration_page):
            self.pages.addWidget(page)
        right.addWidget(self.pages)

        console_host = QWidget()
        console_layout = QVBoxLayout(console_host)
        console_layout.setContentsMargins(20, 8, 20, 16)
        self.console = DiagnosticConsole()
        console_layout.addWidget(self.console)
        right.addWidget(console_host)
        right.setSizes([640, 220])
        outer.addWidget(right, 1)

        self.setCentralWidget(central)

        self.models_page.reimportRequested.connect(self.open_model_directory)
        self.models_page.rescanRequested.connect(self.rescan_models)
        self.routing_page.routingChanged.connect(self._on_routing_changed)
        self.build_page.buildRequested.connect(self.start_build)
        self.calibration_page.connectRequested.connect(self.toggle_xcp_link)
        self.calibration_page.switchPageRequested.connect(self.apply_calibration)
        self.calibration_page.recordToggled.connect(self.toggle_recording)

    # -- startup ------------------------------------------------------------

    def _check_dependencies(self) -> None:
        statuses = check_dependencies()
        self.build_page.set_dependencies(statuses)
        required_ok = all(s.ok for s in statuses
                          if s.required and s.name != "python")
        missing = [s.name for s in statuses
                   if s.required and not s.ok and s.name != "python"]
        self.sidebar.toolchain_chip.set_state(
            "Toolchain OK" if required_ok
            else f"{len(missing)} tool(s) missing",
            theme.OK if required_ok else theme.ERR)
        if missing:
            self.sidebar.toolchain_chip.label.setToolTip(
                "missing: " + ", ".join(missing))
        for status in statuses:
            self.console.append_line(
                f"[dep] {status.name} {status.version or '—'} "
                f"{'ok' if status.ok else 'MISSING'} {status.detail}".rstrip())

        matlab = next((s for s in statuses if s.name == "matlab"), None)
        if matlab is not None:
            self.models_page.set_matlab_state(
                "MATLAB available" if matlab.ok else "MATLAB not found",
                theme.OK if matlab.ok else theme.TEXT_MUTED)

    def _navigate(self, index: int) -> None:
        self.pages.setCurrentIndex(index)

    # -- workspace ----------------------------------------------------------

    def open_workspace(self, path: Path) -> None:
        """Open a workspace, offering migration when the schema is old."""
        try:
            if ws.needs_migration(path):
                version = ws.peek_version(path)
                answer = QMessageBox.question(
                    self, "Migrate workspace?",
                    f"{path.name} uses routing schema v{version}; this build "
                    f"uses v{ws.WORKSPACE_SCHEMA_VERSION}.\n\nMigration is "
                    f"automatic and keeps a backup as "
                    f"{ws.backup_path(path).name}.",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    self.console.append_line(
                        f"[workspace] {path.name}: migration declined; "
                        f"not opened")
                    return
                workspace = ws.load(path, migrate=True)
                self.console.append_line(
                    f"[workspace] migrated v{workspace.migrated_from} → "
                    f"v{ws.WORKSPACE_SCHEMA_VERSION} (backup kept)")
            else:
                workspace = ws.load(path)
        except ws.WorkspaceError as exc:
            self.console.append_line(f"[workspace] error: {exc}")
            QMessageBox.warning(self, "Workspace", str(exc))
            return

        self.workspace = workspace
        self.sidebar.workspace_label.setText(path.name)
        self._apply_workspace()

    def _apply_workspace(self) -> None:
        descriptor = self.workspace.descriptor
        self.models_page.set_descriptor(descriptor)
        self.routing_page.load(descriptor, self.workspace.routing,
                               self.hal_manifest)
        self.build_page.set_blocked_models(self.models_page.blocked_models())
        self._update_sizing()

    def _update_sizing(self) -> None:
        if not self.workspace.descriptor.get("models"):
            return
        report = sz.estimate_footprint(self.workspace.descriptor,
                                       self.workspace.routing)
        self.build_page.set_sizing(report)

    def _on_routing_changed(self) -> None:
        self.workspace.routing = self.routing_page.model.to_routing()
        self._update_sizing()

    # -- actions ------------------------------------------------------------

    def open_model_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select model directory")
        if not directory:
            return
        self.workspace.model_dir = directory
        self.console.append_line(f"[models] scanning {directory}")
        self.rescan_models()

    def rescan_models(self) -> None:
        """Extraction runs off the UI thread (GUI-002); without MATLAB this
        reports honestly rather than pretending to scan."""
        self.console.append_line(
            "[models] extraction requires a MATLAB session; "
            "load a workspace descriptor instead")

    def start_build(self) -> None:
        reasons = self.build_page.gate_reasons()
        if reasons:
            for reason in reasons:
                self.console.append_line(f"[build] blocked: {reason}")
            return
        self.console.append_line("[build] starting…")

    # -- live tuning (GUI-012) ----------------------------------------------

    def recording_path(self) -> Path:
        """Next to the workspace when there is one, else the working dir."""
        stem = time.strftime("picodesk_%Y%m%d_%H%M%S.mf4")
        base = self.workspace.path.parent if self.workspace.path else Path.cwd()
        return base / stem

    def toggle_xcp_link(self) -> None:
        if self.xcp is not None and self.xcp.connected:
            self.disconnect_xcp()
            return
        port = self.xcp_port
        self.calibration_page.set_link_state(f"XCP — connecting {port}",
                                             theme.TEXT_MUTED)
        self.console.append_line(f"[xcp] connecting on {port}")

        def connect(emit_line: Any) -> dict[str, float]:
            master = XcpMaster(self._xcp_backend_factory(port))
            master.load_parameters(self.xcp_parameters)
            master.connect()
            emit_line(f"[xcp] connected on {port}")
            return {"master": master, "values": master.read_all()}

        submit(connect, on_line=self.console.append_line,
               on_finished=self._on_xcp_connected,
               on_failed=self._on_xcp_failed, pool=self.pool)

    def _on_xcp_connected(self, result: dict[str, Any]) -> None:
        self.xcp = result["master"]
        self.calibration_page.set_link_state("XCP — connected", theme.OK)
        self.calibration_page.set_parameters(result["values"])
        self.calibration_page.set_signals([s.name for s in self.xcp_daq_signals])
        self.calibration_page.connect_button.setText("Disconnect")

    def _on_xcp_failed(self, message: str) -> None:
        """A missing board is the normal case off the bench, so it reports
        rather than raising: the panel stays usable and says why."""
        self.xcp = None
        self.calibration_page.set_link_state("XCP — disconnected", theme.ERR)
        self.calibration_page.connect_button.setText("Connect")
        for line in message.strip().splitlines()[-2:]:
            self.console.append_line(f"[xcp] {line}")

    def disconnect_xcp(self) -> None:
        if self.recorder.recording:
            self.toggle_recording(False)
        if self.xcp is not None:
            try:
                self.xcp.disconnect()
            finally:
                self.xcp = None
        self.calibration_page.set_link_state("XCP — disconnected",
                                             theme.TEXT_MUTED)
        self.calibration_page.connect_button.setText("Connect")
        self.console.append_line("[xcp] disconnected")

    def apply_calibration(self) -> None:
        """Download the pending edits, then arm the page switch (RTE-003).

        The panel does not mark the edits live here. Core 0 commits at a
        model_step() boundary on its own schedule, so the commit is confirmed
        by reading the page back.
        """
        if self.xcp is None or not self.xcp.connected:
            self.console.append_line("[xcp] not connected; edits not applied")
            return
        edits = dict(self.calibration_page.session.offline_edits)
        if not edits:
            return
        master = self.xcp

        def apply(emit_line: Any) -> bool:
            for name, value in edits.items():
                master.write_parameter(name, value)
                emit_line(f"[xcp] offline page: {name} = {value}")
            target_page = master.active_page() ^ 1
            master.request_page_switch()
            for _ in range(50):  # ~0.5 s of 1 kHz step boundaries
                if master.switch_committed(target_page):
                    emit_line(f"[xcp] page switch committed -> {target_page}")
                    return True
                time.sleep(0.01)
            raise TimeoutError("target did not report the CAL page switch")

        submit(apply, on_line=self.console.append_line,
               on_finished=self._on_calibration_applied,
               on_failed=self._on_xcp_failed, pool=self.pool)

    def _on_calibration_applied(self, committed: bool) -> None:
        if committed:
            self.calibration_page.session.commit()
            self.calibration_page.refresh_parameters()

    def toggle_recording(self, recording: bool) -> None:
        if recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self) -> None:
        if self.xcp is None or not self.xcp.connected:
            self.console.append_line("[xcp] not connected; nothing to record")
            return
        chosen = set(self.calibration_page.selected_signals())
        signals = [s for s in self.xcp_daq_signals if s.name in chosen]
        if not signals:
            self.console.append_line("[xcp] select at least one DAQ signal")
            return

        self.xcp.configure_daq(signals)
        self.xcp.start_daq()
        self.recorder.start([SignalSpec(s.name, "i4") for s in signals])

        self._daq_timer = QTimer(self)
        self._daq_timer.timeout.connect(self._drain_daq)
        self._daq_timer.start(50)
        self.console.append_line(
            f"[xcp] recording {len(signals)} signal(s) to MDF4")

    def _drain_daq(self) -> None:
        if self.xcp is None or not self.recorder.recording:
            return
        for timestamp, values in self.xcp.poll():
            self.recorder.append(timestamp, values)

    def _stop_recording(self) -> None:
        if self._daq_timer is not None:
            self._daq_timer.stop()
            self._daq_timer = None
        if self.xcp is not None:
            self.xcp.stop_daq()
        if not self.recorder.recording:
            return
        out = self.recording_path()
        try:
            written = self.recorder.stop(out)
        except Exception as exc:  # noqa: BLE001 — an empty capture is not fatal
            self.console.append_line(f"[xcp] recording not saved: {exc}")
            return
        note = (f" ({self.recorder.dropped} frame(s) dropped at the cap)"
                if self.recorder.dropped else "")
        self.console.append_line(f"[xcp] wrote {written}{note}")

    def load_descriptor_file(self, path: Path) -> None:
        """Convenience for demos and tests: adopt a descriptor directly."""
        self.workspace.descriptor = json.loads(path.read_text(encoding="utf-8"))
        self._apply_workspace()
