"""Build page (7d): toolchain, pipeline, sizing report, reproducibility.

The primary action is gated exactly as the requirements demand: builds are
disabled unless every required dependency is present (GUI-004), no model is
blocked by MAT-002, and the static sizing estimate fits the budget
(BLD-004). The reason a build is unavailable is always stated, never left
for the user to infer from a greyed-out button.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from picodesk.buildsys import sizing_report as sz
from picodesk.buildsys.dependency_checker import DependencyStatus, builds_allowed
from picodesk.gui import theme
from picodesk.gui.widgets import Badge, Panel, StatusChip, UsageBar

STAGES = [
    ("Descriptor scan", "hash every .slx; skip unchanged (GUI-001, NFR-2)"),
    ("ERT code generation", "regenerate only changed models"),
    ("RTE generation", "dispatch, seqlocks, CAL pages, DAQ ring"),
    ("CMake configure", "-ffile-prefix-map, __DATE__ banned (BLD-008)"),
    ("Compile + link", "custom rp2040_banked.ld (BLD-002)"),
    ("UF2 pack + hash", "SHA-256 compared against the previous build"),
]


class BuildPage(QWidget):
    buildRequested = pyqtSignal()
    flashRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dependencies: list[DependencyStatus] = []
        self._blocked_models: list[str] = []
        self._report: sz.SizingReport | None = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Build & Deploy")
        title.setObjectName("Title")
        header.addWidget(title)
        self.subtitle = QLabel("no build yet")
        self.subtitle.setObjectName("Subtitle")
        header.addWidget(self.subtitle)
        header.addStretch(1)
        self.repro_chip = StatusChip("Reproducibility unknown", theme.TEXT_MUTED)
        header.addWidget(self.repro_chip)
        self.flash_button = QPushButton("Flash UF2")
        self.flash_button.setEnabled(False)
        self.flash_button.clicked.connect(self.flashRequested)
        header.addWidget(self.flash_button)
        self.build_button = QPushButton("Build Firmware")
        self.build_button.setObjectName("Primary")
        self.build_button.clicked.connect(self.buildRequested)
        header.addWidget(self.build_button)
        layout.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(16)

        self.toolchain_panel = Panel("TOOLCHAIN", "checked at startup · GUI-004")
        self.toolchain_rows = QVBoxLayout()
        self.toolchain_rows.setSpacing(6)
        self.toolchain_panel.body.addLayout(self.toolchain_rows)
        left.addWidget(self.toolchain_panel)

        pipeline = Panel("PIPELINE", "hash-gated · NFR-2 budget 45 s")
        for name, detail in STAGES:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            text = QVBoxLayout()
            stage = QLabel(name)
            stage.setStyleSheet("font-weight:500;")
            text.addWidget(stage)
            hint = QLabel(detail)
            hint.setObjectName("Muted")
            hint.setStyleSheet("font-size:10px;")
            text.addWidget(hint)
            row_layout.addLayout(text, 1)
            pipeline.body.addWidget(row)
        left.addWidget(pipeline)
        left.addStretch(1)
        columns.addLayout(left, 1)

        self.sizing_panel = Panel("STATIC SIZING REPORT", "pre-CMake · BLD-004")
        self.sizing_panel.setFixedWidth(380)
        self.bars: dict[str, UsageBar] = {}
        for bank in ("SRAM total", "SRAM0 · Core 0", "SRAM1 · Core 1",
                     "SRAM2 · shared", "Flash"):
            bar = UsageBar(bank)
            self.bars[bank] = bar
            self.sizing_panel.body.addWidget(bar)
        self.sizing_verdict = QLabel("no estimate yet")
        self.sizing_verdict.setObjectName("Muted")
        self.sizing_verdict.setWordWrap(True)
        self.sizing_panel.body.addWidget(self.sizing_verdict)
        self.sizing_panel.body.addStretch(1)
        columns.addWidget(self.sizing_panel)

        layout.addLayout(columns, 1)

    # -- API ----------------------------------------------------------------

    def set_dependencies(self, statuses: list[DependencyStatus]) -> None:
        self._dependencies = statuses
        while self.toolchain_rows.count():
            item = self.toolchain_rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for status in statuses:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            mark = QLabel("✓" if status.ok else ("!" if status.found else "✗"))
            mark.setStyleSheet(
                f"color:{theme.OK if status.ok else (theme.WARN if not status.required else theme.ERR)};"
                f"font-weight:600;")
            row_layout.addWidget(mark)
            name = QLabel(status.name)
            name.setStyleSheet("font-weight:500;")
            row_layout.addWidget(name)
            version = QLabel(status.version or "—")
            version.setStyleSheet(
                f"font-family:{theme.FONT_MONO_STACK}; font-size:11px;"
                f"color:{theme.TEXT_SECONDARY};")
            row_layout.addWidget(version)
            row_layout.addStretch(1)
            if not status.required:
                row_layout.addWidget(Badge("OPTIONAL"))
            if status.detail:
                detail = QLabel(status.detail)
                detail.setObjectName("Muted")
                detail.setStyleSheet(
                    f"font-family:{theme.FONT_MONO_STACK}; font-size:10px;")
                row_layout.addWidget(detail)
            self.toolchain_rows.addWidget(row)

        ok = builds_allowed(statuses)
        self.toolchain_panel.set_subtitle(
            "all required present" if ok else "missing required tools")
        self._update_gate()

    def set_blocked_models(self, names: list[str]) -> None:
        self._blocked_models = list(names)
        self._update_gate()

    def set_sizing(self, report: sz.SizingReport) -> None:
        self._report = report
        banks = report.banks
        self.bars["SRAM total"].set_usage(
            report.sram_total, sz.SRAM_LIMIT_BYTES, "halt above 200 kB")
        for key, bank, note in (
            ("SRAM0 · Core 0", "SRAM0", "core 0 stack + fast-path state"),
            ("SRAM1 · Core 1", "SRAM1", "FreeRTOS task stacks"),
            ("SRAM2 · shared", "SRAM2", "seqlocks · DAQ ring · CAL pages · heap"),
        ):
            self.bars[key].set_usage(banks.get(bank, 0), sz.BANK_SIZE_BYTES, note)
        self.bars["Flash"].set_usage(
            report.flash_total, sz.FLASH_LIMIT_BYTES,
            "XIP · fast path pinned to SRAM (BLD-001)")

        if report.ok:
            self.sizing_verdict.setText("estimate fits the budget")
            self.sizing_verdict.setStyleSheet(f"color:{theme.OK};")
        else:
            self.sizing_verdict.setText(
                "BUILD HALTED — " + "; ".join(report.blocking_reasons()))
            self.sizing_verdict.setStyleSheet(f"color:{theme.ERR};")
        self._update_gate()

    def set_reproducible(self, identical: bool, digest: str) -> None:
        if identical:
            self.repro_chip.set_state(
                f"Reproducible · {digest[:12]}…", theme.OK)
        else:
            self.repro_chip.set_state("UF2 differs between builds", theme.ERR)

    def set_artifact_ready(self, ready: bool) -> None:
        self.flash_button.setEnabled(ready)

    def gate_reasons(self) -> list[str]:
        """Why a build cannot start right now — empty means it can."""
        reasons: list[str] = []
        if self._dependencies and not builds_allowed(self._dependencies):
            missing = [s.name for s in self._dependencies
                       if s.required and not s.ok and s.name != "python"]
            reasons.append(f"toolchain incomplete: {', '.join(missing)} "
                           f"(GUI-004)")
        if self._blocked_models:
            reasons.append(
                f"{', '.join(self._blocked_models)} blocked by MAT-002")
        if self._report is not None and not self._report.ok:
            reasons.extend(self._report.blocking_reasons())
        return reasons

    def _update_gate(self) -> None:
        reasons = self.gate_reasons()
        self.build_button.setEnabled(not reasons)
        self.build_button.setToolTip(
            "\n".join(reasons) if reasons else "build the firmware")
        if reasons:
            self.subtitle.setText(reasons[0])
