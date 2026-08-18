"""Models page (7b): import, hash/staleness, MAT-002 blocking, I/O detail.

Mirrors the Pencil deck: a model table whose STATE column is the honest
answer to "can this model be built right now?" — Fresh, Stale (hash moved
since extraction, GUI-001), or Blocked (float in a fast-loop model,
MAT-002) — beside a detail panel for the selected model.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from picodesk.gui import theme
from picodesk.gui.widgets import Badge, Panel, StatusChip
from picodesk.matlab_bridge.descriptor import FLOAT_TYPES

COLUMNS = ["MODEL", "RATE", "GROUP", "I / O", "TYPES", "HASH", "STATE"]


def model_state(name: str, model: dict[str, Any],
                stale: bool) -> tuple[str, str]:
    """(label, colour) for the STATE column."""
    offenders = [p["data_type"] for p in
                 model["inports"] + model["outports"]
                 if p["data_type"] in FLOAT_TYPES]
    offenders += [t for t in model.get("internal_types", [])
                  if t in FLOAT_TYPES]
    if model["rate_group"] == "fast_1ms" and offenders:
        return "Blocked · MAT-002", theme.ERR
    if stale:
        return "Stale — re-export", theme.WARN
    return "Fresh", theme.OK


class ModelsPage(QWidget):
    reimportRequested = pyqtSignal()
    rescanRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.descriptor: dict[str, Any] = {}
        self.stale: set[str] = set()
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("Models")
        title.setObjectName("Title")
        header.addWidget(title)
        self.subtitle = QLabel("no workspace loaded")
        self.subtitle.setObjectName("Subtitle")
        header.addWidget(self.subtitle)
        header.addStretch(1)
        self.matlab_chip = StatusChip("MATLAB — not connected", theme.TEXT_MUTED)
        header.addWidget(self.matlab_chip)
        rescan = QPushButton("Re-scan")
        rescan.clicked.connect(self.rescanRequested)
        header.addWidget(rescan)
        import_button = QPushButton("Import Models…")
        import_button.setObjectName("Primary")
        import_button.clicked.connect(self.reimportRequested)
        header.addWidget(import_button)
        layout.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(16)

        table_panel = Panel("MODEL TABLE")
        self.table = QTreeWidget()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHeaderLabels(COLUMNS)
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(False)
        self.table.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(COLUMNS)):
            self.table.header().setSectionResizeMode(
                i, QHeaderView.ResizeMode.ResizeToContents)
        # setItemWidget cells are invisible to ResizeToContents, so the rate
        # badge needs its width stated or the label is clipped mid-word.
        self.table.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 120)
        self.table.currentItemChanged.connect(
            lambda cur, _prev: self._show_detail(cur))
        table_panel.body.addWidget(self.table)
        content.addWidget(table_panel, 1)

        self.detail = Panel("MODEL DETAIL")
        self.detail.setFixedWidth(330)
        self.detail_body = QVBoxLayout()
        self.detail_body.setSpacing(8)
        self.detail.body.addLayout(self.detail_body)
        self.detail.body.addStretch(1)
        note = QLabel("Fast-loop models may not use single/double — the "
                      "RP2040 has no FPU (MAT-002).")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        note.setStyleSheet("font-size:11px;")
        self.detail.body.addWidget(note)
        content.addWidget(self.detail)

        layout.addLayout(content, 1)

    # -- API ----------------------------------------------------------------

    def set_matlab_state(self, text: str, colour: str) -> None:
        self.matlab_chip.set_state(text, colour)

    def set_descriptor(self, descriptor: dict[str, Any],
                       stale: set[str] | None = None) -> None:
        self.descriptor = descriptor or {}
        self.stale = stale or set()
        self._populate()

    def blocked_models(self) -> list[str]:
        return [name for name, model in self.descriptor.get("models", {}).items()
                if model_state(name, model, name in self.stale)[0].startswith(
                    "Blocked")]

    # -- rendering ----------------------------------------------------------

    def _populate(self) -> None:
        self.table.clear()
        models = self.descriptor.get("models", {})
        blocked = stale_count = 0

        for name in sorted(models):
            model = models[name]
            is_stale = name in self.stale
            state, colour = model_state(name, model, is_stale)
            blocked += state.startswith("Blocked")
            stale_count += is_stale

            types = sorted({p["data_type"] for p in
                            model["inports"] + model["outports"]})
            item = QTreeWidgetItem([
                name,
                f"{model['base_rate_s'] * 1000:g} ms",
                "",
                f"{len(model['inports'])} / {len(model['outports'])}",
                " · ".join(types),
                (model.get("slx_sha256") or "")[:8],
                "",  # column 6 is rendered by the coloured state widget below
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            for column in (1, 3, 4, 5):
                item.setFont(column, self._mono(item.font(column)))
            self.table.addTopLevelItem(item)
            self.table.setItemWidget(
                item, 2, Badge.for_rate_group(model["rate_group"]))
            state_label = QLabel(f"● {state}")
            state_label.setStyleSheet(f"color:{colour};")
            self.table.setItemWidget(item, 6, state_label)

        shown = len(models)
        self.subtitle.setText(
            f"{shown} model{'s' if shown != 1 else ''} · {stale_count} stale · "
            f"{blocked} blocked")
        if self.table.topLevelItemCount():
            self.table.setCurrentItem(self.table.topLevelItem(0))
        else:
            self._show_detail(None)

    @staticmethod
    def _mono(font):
        font.setFamily(theme.FONT_MONO)
        return font

    def _clear_detail(self) -> None:
        while self.detail_body.count():
            item = self.detail_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _show_detail(self, item: QTreeWidgetItem | None) -> None:
        self._clear_detail()
        if item is None:
            empty = QLabel("Import a model directory to begin.")
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            self.detail_body.addWidget(empty)
            return

        name = item.data(0, Qt.ItemDataRole.UserRole)
        model = self.descriptor["models"][name]

        heading = QLabel(name)
        heading.setStyleSheet("font-size:15px; font-weight:600;")
        self.detail_body.addWidget(heading)
        self.detail_body.addWidget(
            Badge.for_rate_group(model["rate_group"]))

        for line in (f"{model.get('file', name + '.slx')}",
                     f"SHA-256 {(model.get('slx_sha256') or '')[:12]}…",
                     f"base rate {model['base_rate_s'] * 1000:g} ms"):
            label = QLabel(line)
            label.setObjectName("Muted")
            label.setStyleSheet(
                f"font-family:{theme.FONT_MONO_STACK}; font-size:10px;")
            self.detail_body.addWidget(label)

        for caption, ports in (("OUTPORTS", model["outports"]),
                               ("INPORTS", model["inports"])):
            header = QLabel(f"{caption} · {len(ports)}")
            header.setObjectName("PanelHeader")
            self.detail_body.addWidget(header)
            for port in ports:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                port_name = QLabel(port["name"])
                port_name.setStyleSheet(
                    f"font-family:{theme.FONT_MONO_STACK}; font-size:12px;")
                row_layout.addWidget(port_name)
                row_layout.addStretch(1)
                is_float = port["data_type"] in FLOAT_TYPES
                bad = is_float and model["rate_group"] == "fast_1ms"
                row_layout.addWidget(Badge(
                    port["data_type"].upper(),
                    theme.ERR if bad else theme.TEXT_SECONDARY,
                    theme.ERR_SOFT if bad else theme.BG_ELEVATED))
                self.detail_body.addWidget(row)
