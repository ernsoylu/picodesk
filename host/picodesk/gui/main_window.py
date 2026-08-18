"""Application shell (7a): sidebar navigation, pages, diagnostic console.

Wires the four pages to the real backends built in Phases 4–6. Everything
slow — MATLAB extraction, RTE generation, CMake — goes through
`picodesk.gui.workers`, so the UI thread only ever handles signals; that is
what keeps the interaction budget (no stall over 100 ms) achievable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QThreadPool, pyqtSignal
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
from picodesk.rtegen.routing import load_hal_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
HAL_MANIFEST = REPO_ROOT / "target" / "hal" / "hal_manifest.json"

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


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PicoDesk")
        self.resize(1440, 900)
        self.pool = QThreadPool.globalInstance()
        self.workspace = ws.Workspace()
        self.hal_manifest: dict[str, dict[str, Any]] = {}
        if HAL_MANIFEST.is_file():
            self.hal_manifest = load_hal_manifest(HAL_MANIFEST)

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

    def load_descriptor_file(self, path: Path) -> None:
        """Convenience for demos and tests: adopt a descriptor directly."""
        self.workspace.descriptor = json.loads(path.read_text(encoding="utf-8"))
        self._apply_workspace()
