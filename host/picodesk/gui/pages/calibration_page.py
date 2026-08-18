"""Calibration / live tuning page (7e, GUI-012).

Telemetry tiles (BLD-003) across the top, the CAL-page parameter editor on
the left, DAQ signal selection and MDF4 recording on the right.

The calibration workflow is the transactional one the target implements
(RTE-003): edits land on the OFFLINE page and stay invisible to the running
loop until "Switch Page" is pressed, at which point the fast ISR commits
them at a model_step boundary. The banner always names the page being
edited and how many changes are pending, so nobody has to guess whether a
value is live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from picodesk.gui import theme
from picodesk.gui.widgets import MetricTile, Panel, StatusChip


@dataclass
class CalibrationSession:
    """Qt-free model of the offline-page edit set (RTE-003)."""

    active_values: dict[str, float] = field(default_factory=dict)
    offline_edits: dict[str, float] = field(default_factory=dict)
    active_page: int = 0
    switch_pending: bool = False

    def edit(self, name: str, value: float) -> None:
        if name not in self.active_values:
            raise KeyError(name)
        if value == self.active_values[name]:
            self.offline_edits.pop(name, None)
        else:
            self.offline_edits[name] = value

    @property
    def pending(self) -> int:
        return len(self.offline_edits)

    def request_switch(self) -> bool:
        """Arm SET_CAL_PAGE; refuses when there is nothing to apply."""
        if not self.offline_edits:
            return False
        self.switch_pending = True
        return True

    def commit(self) -> None:
        """Called when the target reports the switch committed."""
        self.active_values.update(self.offline_edits)
        self.offline_edits.clear()
        self.switch_pending = False
        self.active_page ^= 1

    def discard(self) -> None:
        self.offline_edits.clear()
        self.switch_pending = False

    def display_value(self, name: str) -> tuple[float, bool]:
        """(value, modified) — the offline value wins while pending."""
        if name in self.offline_edits:
            return self.offline_edits[name], True
        return self.active_values.get(name, 0.0), False


class CalibrationPage(QWidget):
    connectRequested = pyqtSignal()
    switchPageRequested = pyqtSignal()
    recordToggled = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = CalibrationSession()
        self._recording = False
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Live Tuning")
        title.setObjectName("Title")
        header.addWidget(title)
        subtitle = QLabel("XCP on CDC · A2L patched from DWARF (CAL-002)")
        subtitle.setObjectName("Subtitle")
        header.addWidget(subtitle)
        header.addStretch(1)
        self.link_chip = StatusChip("XCP — disconnected", theme.TEXT_MUTED)
        header.addWidget(self.link_chip)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.connectRequested)
        header.addWidget(self.connect_button)
        self.record_button = QPushButton("Record MDF4")
        self.record_button.clicked.connect(self._toggle_record)
        header.addWidget(self.record_button)
        layout.addLayout(header)

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.tiles = {
            "isr": MetricTile("ISR UTILIZATION", "—", "%",
                              "worst execution time of the 1 ms budget"),
            "overrun": MetricTile("OVERRUNS", "—", "",
                                  "missed fast-loop deadlines (BLD-003)"),
            "seqlock": MetricTile("SEQLOCK FAULTS", "—", "",
                                  "stale fallbacks (RTE-004)"),
            "daq": MetricTile("DAQ THROUGHPUT", "—", "Hz",
                              "coherent frames drained (RTE-005)"),
            "watchdog": MetricTile("WATCHDOG", "—", "",
                                   "core 0 heartbeat (BLD-007)"),
        }
        for tile in self.tiles.values():
            tiles.addWidget(tile)
        layout.addLayout(tiles)

        panes = QHBoxLayout()
        panes.setSpacing(14)

        self.param_panel = Panel("PARAMETERS", "offline page · RTE-003")
        banner = QWidget()
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(10, 8, 10, 8)
        self.banner_label = QLabel("No pending changes")
        self.banner_label.setWordWrap(True)
        banner_layout.addWidget(self.banner_label, 1)
        self.discard_button = QPushButton("Discard")
        self.discard_button.clicked.connect(self._discard)
        banner_layout.addWidget(self.discard_button)
        self.switch_button = QPushButton("Switch Page")
        self.switch_button.setObjectName("Primary")
        self.switch_button.clicked.connect(self._request_switch)
        banner_layout.addWidget(self.switch_button)
        banner.setStyleSheet(
            f"background:{theme.ACCENT_SOFT}; border-radius:6px;")
        self.param_panel.body.addWidget(banner)

        self.param_table = QTreeWidget()
        self.param_table.setColumnCount(4)
        self.param_table.setHeaderLabels(
            ["PARAMETER", "VALUE", "ACTIVE", "STATE"])
        self.param_table.setRootIsDecorated(False)
        self.param_table.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.param_table.itemChanged.connect(self._on_item_changed)
        self.param_panel.body.addWidget(self.param_table, 1)
        panes.addWidget(self.param_panel, 1)

        self.daq_panel = Panel("DAQ · SIGNALS", "select what to stream")
        self.daq_panel.setFixedWidth(360)
        self.signal_list = QListWidget()
        self.daq_panel.body.addWidget(self.signal_list, 1)
        self.record_label = QLabel("not recording")
        self.record_label.setObjectName("Muted")
        self.record_label.setStyleSheet(
            f"font-family:{theme.FONT_MONO_STACK}; font-size:10px;")
        self.daq_panel.body.addWidget(self.record_label)
        panes.addWidget(self.daq_panel)

        layout.addLayout(panes, 1)
        self._update_banner()

    # -- API ----------------------------------------------------------------

    def set_link_state(self, text: str, colour: str) -> None:
        self.link_chip.set_state(text, colour)

    def set_parameters(self, values: dict[str, float]) -> None:
        self.session = CalibrationSession(active_values=dict(values))
        self._render_parameters()
        self._update_banner()

    def set_signals(self, names: list[str],
                    rate_groups: dict[str, str] | None = None) -> None:
        self.signal_list.clear()
        rate_groups = rate_groups or {}
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            group = rate_groups.get(name)
            if group:
                item.setToolTip(theme.RATE_LABELS.get(group, group))
            font = item.font()
            font.setFamily(theme.FONT_MONO)
            item.setFont(font)
            self.signal_list.addItem(item)

    def selected_signals(self) -> list[str]:
        return [self.signal_list.item(i).text()
                for i in range(self.signal_list.count())
                if self.signal_list.item(i).checkState()
                == Qt.CheckState.Checked]

    def update_telemetry(self, telemetry: dict[str, Any]) -> None:
        exec_us = telemetry.get("exec_max_us")
        if exec_us is not None:
            utilisation = 100.0 * exec_us / 1000.0
            colour = (theme.ERR if utilisation > 90 else
                      theme.WARN if utilisation > 70 else theme.TEXT_PRIMARY)
            self.tiles["isr"].set_value(f"{utilisation:.1f}", colour)
            self.tiles["isr"].sub.setText(f"{exec_us} µs of the 1 ms budget")

        overruns = telemetry.get("overrun_count")
        if overruns is not None:
            self.tiles["overrun"].set_value(
                str(overruns), theme.ERR if overruns else theme.OK)

        faults = telemetry.get("seqlock_fault_count")
        if faults is not None:
            self.tiles["seqlock"].set_value(
                str(faults), theme.WARN if faults else theme.OK)

        daq = telemetry.get("daq_rate_hz")
        if daq is not None:
            self.tiles["daq"].set_value(f"{daq:.0f}")

        heartbeat = telemetry.get("heartbeat_delta")
        if heartbeat is not None:
            alive = heartbeat > 0
            self.tiles["watchdog"].set_value(
                "OK" if alive else "STALLED", theme.OK if alive else theme.ERR)
            self.tiles["watchdog"].sub.setText(
                f"heartbeat +{heartbeat}/s (BLD-007)")

    def notify_switch_committed(self) -> None:
        """The target reported the CAL page swap landed (RTE-003)."""
        self.session.commit()
        self._render_parameters()
        self._update_banner()

    # -- internals ----------------------------------------------------------

    def _render_parameters(self) -> None:
        self.param_table.blockSignals(True)
        self.param_table.clear()
        for name in sorted(self.session.active_values):
            value, modified = self.session.display_value(name)
            active = self.session.active_values[name]
            item = QTreeWidgetItem(
                [name, f"{value:g}", f"{active:g}",
                 "modified" if modified else ""])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            for column in (0, 1, 2):
                font = item.font(column)
                font.setFamily(theme.FONT_MONO)
                item.setFont(column, font)
            if modified:
                item.setForeground(1, Qt.GlobalColor.cyan)
            self.param_table.addTopLevelItem(item)
        self.param_table.blockSignals(False)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 1:
            return
        name = item.text(0)
        try:
            self.session.edit(name, float(item.text(1)))
        except (ValueError, KeyError):
            pass  # revert below
        self._render_parameters()
        self._update_banner()

    def _update_banner(self) -> None:
        pending = self.session.pending
        page = "B" if self.session.active_page == 0 else "A"
        if self.session.switch_pending:
            self.banner_label.setText(
                f"Switch armed — the fast loop commits page {page} at the "
                f"next model_step boundary (RTE-003)")
        elif pending:
            self.banner_label.setText(
                f"Editing offline page RAM_CAL_{page} · {pending} pending "
                f"change{'s' if pending != 1 else ''} — invisible to the "
                f"running loop until the page switch")
        else:
            self.banner_label.setText(
                f"No pending changes · active page RAM_CAL_"
                f"{'A' if self.session.active_page == 0 else 'B'}")
        self.switch_button.setEnabled(pending > 0
                                      and not self.session.switch_pending)
        self.discard_button.setEnabled(pending > 0)

    def _request_switch(self) -> None:
        if self.session.request_switch():
            self._update_banner()
            self.switchPageRequested.emit()

    def _discard(self) -> None:
        self.session.discard()
        self._render_parameters()
        self._update_banner()

    def _toggle_record(self) -> None:
        self._recording = not self._recording
        self.record_button.setText(
            "Stop Recording" if self._recording else "Record MDF4")
        self.record_label.setText(
            "recording → workspace.mf4 (asammdf)" if self._recording
            else "not recording")
        self.recordToggled.emit(self._recording)
