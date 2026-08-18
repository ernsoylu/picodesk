"""Routing matrix page (7c): the 3-pane VFB editor (GUI-007 … GUI-011).

Producers | Connections | Consumers, exactly as the Pencil deck lays it out.
Selecting a producer filters the consumer pane in place (GUI-008): matching
ports stay live, mismatches grey out and say why, already-bound inports show
a lock (GUI-009). Connections carry their mechanism badge (GUI-010), and
"Suggest Bindings" opens a preview that applies or undoes as one step
(GUI-011).

All rules come from RoutingModel; this file only renders them.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from picodesk.gui import theme
from picodesk.gui.routing_model import Connection, RoutingModel
from picodesk.gui.widgets import Badge, Panel


class SuggestionDialog(QDialog):
    """GUI-011 preview: check what to wire, apply as one undoable step."""

    def __init__(self, suggestions: list[tuple[Connection, str]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Suggest Bindings")
        self.setMinimumWidth(620)
        self._checks: list[tuple[QCheckBox, Connection]] = []

        layout = QVBoxLayout(self)
        heading = QLabel(f"{len(suggestions)} exact name + type matches")
        heading.setStyleSheet("font-size:15px; font-weight:600;")
        layout.addWidget(heading)
        note = QLabel("Ambiguous matches are deliberately left out — a wrong "
                      "bulk wire costs more than a missing one.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(4)
        for connection, why in suggestions:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            check = QCheckBox()
            check.setChecked(True)
            row_layout.addWidget(check)
            label = QLabel(f"{connection.producer}  →  {connection.consumer}")
            label.setStyleSheet(
                f"font-family:{theme.FONT_MONO_STACK}; font-size:12px;")
            row_layout.addWidget(label)
            row_layout.addStretch(1)
            fg, bg = ((theme.WARN, theme.WARN_SOFT) if "ZOH" in why
                      else (theme.OK, theme.OK_SOFT))
            row_layout.addWidget(Badge(why.upper(), fg, bg))
            inner_layout.addWidget(row)
            self._checks.append((check, connection))
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Apply Bindings")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName(
            "Primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> list[Connection]:
        return [c for check, c in self._checks if check.isChecked()]


class RoutingPage(QWidget):
    routingChanged = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = RoutingModel()
        self._last_applied: list[Connection] = []
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Signal Routing")
        title.setObjectName("Title")
        header.addWidget(title)
        self.subtitle = QLabel("no connections")
        self.subtitle.setObjectName("Subtitle")
        header.addWidget(self.subtitle)
        header.addStretch(1)
        self.undo_button = QPushButton("Undo Suggestions")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self._undo_suggestions)
        header.addWidget(self.undo_button)
        suggest = QPushButton("Suggest Bindings")
        suggest.setObjectName("Primary")
        suggest.clicked.connect(self._suggest)
        header.addWidget(suggest)
        layout.addLayout(header)

        panes = QHBoxLayout()
        panes.setSpacing(14)

        self.producer_panel = Panel("PRODUCERS")
        self.producer_panel.setFixedWidth(330)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter signals…")
        self.search.textChanged.connect(lambda _: self._render_producers())
        self.producer_panel.body.addWidget(self.search)
        self.producer_list = QListWidget()
        self.producer_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.producer_list.currentItemChanged.connect(
            lambda *_: self._on_producer_selected())
        self.producer_panel.body.addWidget(self.producer_list, 1)
        panes.addWidget(self.producer_panel)

        self.connection_panel = Panel("CONNECTIONS")
        self.connection_list = QListWidget()
        self.connection_list.itemDoubleClicked.connect(self._unlink_selected)
        self.connection_panel.body.addWidget(self.connection_list, 1)
        hint = QLabel("Cross-rate links insert ZOH via a bounded seqlock "
                      "(RTE-004). Double-click a row to unlink.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size:10px;")
        self.connection_panel.body.addWidget(hint)
        panes.addWidget(self.connection_panel, 1)

        self.consumer_panel = Panel("CONSUMERS")
        self.consumer_panel.setFixedWidth(330)
        self.consumer_list = QListWidget()
        self.consumer_list.itemDoubleClicked.connect(self._connect_selected)
        self.consumer_panel.body.addWidget(self.consumer_list, 1)
        panes.addWidget(self.consumer_panel)

        layout.addLayout(panes, 1)

    # -- API ----------------------------------------------------------------

    def load(self, descriptor: dict[str, Any], routing: dict[str, Any],
             hal_manifest: dict[str, dict[str, Any]] | None = None) -> None:
        self.model = RoutingModel.from_workspace(descriptor, routing,
                                                 hal_manifest)
        self._last_applied = []
        self.undo_button.setEnabled(False)
        self.refresh()

    def refresh(self) -> None:
        self._render_producers()
        self._render_connections()
        self._render_consumers()
        stats = self.model.stats()
        self.subtitle.setText(
            f"{stats['total']} connections · {stats['model_to_model']} "
            f"model ↔ model · {stats['hal']} HAL · {stats['cross_rate']} "
            f"cross-rate · {stats['unbound_consumers']} unbound")

    # -- rendering ----------------------------------------------------------

    def _selected_producer(self) -> str | None:
        item = self.producer_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _render_producers(self) -> None:
        needle = self.search.text().strip().lower()
        previous = self._selected_producer()
        self.producer_list.blockSignals(True)
        self.producer_list.clear()
        for port in self.model.producers:
            if needle and needle not in port.ref.lower():
                continue
            suffix = " · ISR-SAFE" if port.is_hal and port.isr_safe else ""
            item = QListWidgetItem(
                f"{port.ref}    {port.type_label()}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, port.ref)
            item.setToolTip(
                f"{port.ref} — {port.type_label()}"
                + (f" · {theme.RATE_LABELS.get(port.rate_group, '')}"
                   if port.rate_group else " · HAL"))
            self.producer_list.addItem(item)
            if port.ref == previous:
                self.producer_list.setCurrentItem(item)
        self.producer_list.blockSignals(False)
        self.producer_panel.set_subtitle(
            f"{self.producer_list.count()} of {len(self.model.producers)} ports")

    def _render_connections(self) -> None:
        self.connection_list.clear()
        for connection in self.model.connections:
            mechanism = self.model.mechanism(connection)
            prod_group, cons_group = self.model.rate_groups_of(connection)
            item = QListWidgetItem(
                f"{connection.producer}  →  {connection.consumer}\n"
                f"    {self.model.badge(connection)}   "
                f"{theme.RATE_LABELS.get(prod_group, prod_group)} → "
                f"{theme.RATE_LABELS.get(cons_group, cons_group)}")
            item.setData(Qt.ItemDataRole.UserRole, connection.consumer)
            item.setForeground(Qt.GlobalColor.white)
            item.setToolTip(f"mechanism: {mechanism}")
            font = item.font()
            font.setFamily(theme.FONT_MONO)
            font.setPointSize(9)
            item.setFont(font)
            self.connection_list.addItem(item)
        self.connection_panel.set_subtitle(
            f"{len(self.model.connections)} edges · double-click to unlink")

    def _render_consumers(self) -> None:
        producer_ref = self._selected_producer()
        self.consumer_list.clear()
        rows = (self.model.selectable_consumers(producer_ref) if producer_ref
                else [(c, False, "") for c in self.model.consumers])

        for port, selectable, reason in rows:
            bound_to = self.model.writer_of(port.ref)
            lock = "🔒 " if bound_to else ""
            text = f"{lock}{port.ref}    {port.type_label()}"
            if bound_to:
                text += f"\n    ← {bound_to}"
            elif not selectable and reason:
                text += f"\n    {reason}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, port.ref)
            font = item.font()
            font.setFamily(theme.FONT_MONO)
            font.setPointSize(9)
            item.setFont(font)
            if selectable:
                item.setForeground(Qt.GlobalColor.white)
                item.setToolTip("double-click to bind")
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip(reason)
            self.consumer_list.addItem(item)

        selectable_count = sum(1 for _, ok, _ in rows if ok)
        self.consumer_panel.set_subtitle(
            f"{selectable_count} compatible" if producer_ref
            else "select a producer")

    # -- interactions -------------------------------------------------------

    def _on_producer_selected(self) -> None:
        self._render_consumers()

    def _connect_selected(self, item: QListWidgetItem) -> None:
        producer_ref = self._selected_producer()
        if producer_ref is None:
            return
        try:
            self.model.connect(producer_ref, item.data(Qt.ItemDataRole.UserRole))
        except ValueError:
            return  # the row is disabled anyway; nothing to report
        self.refresh()
        self.routingChanged.emit()

    def _unlink_selected(self, item: QListWidgetItem) -> None:
        if self.model.disconnect(item.data(Qt.ItemDataRole.UserRole)):
            self.refresh()
            self.routingChanged.emit()

    def _suggest(self) -> None:
        suggestions = self.model.suggest_bindings()
        if not suggestions:
            self.subtitle.setText("no unambiguous name + type matches found")
            return
        dialog = SuggestionDialog(suggestions, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._last_applied = self.model.apply_suggestions(dialog.selected())
        self.undo_button.setEnabled(bool(self._last_applied))
        self.refresh()
        self.routingChanged.emit()

    def _undo_suggestions(self) -> None:
        self.model.undo_suggestions(self._last_applied)
        self._last_applied = []
        self.undo_button.setEnabled(False)
        self.refresh()
        self.routingChanged.emit()
