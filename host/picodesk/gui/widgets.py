"""Small shared widgets matching the Pencil deck's component set."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from picodesk.gui import theme


class Badge(QLabel):
    """Compact status pill (rate groups, types, states)."""

    def __init__(self, text: str, fg: str = theme.TEXT_SECONDARY,
                 bg: str = theme.BG_ELEVATED,
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:4px; padding:3px 8px;"
            f"font-family:{theme.FONT_MONO_STACK}; font-size:10px;"
            f"font-weight:600; letter-spacing:0.6px;")
        self.setSizePolicy(QSizePolicy.Policy.Maximum,
                           QSizePolicy.Policy.Maximum)

    @classmethod
    def for_rate_group(cls, group: str, parent: QWidget | None = None) -> Badge:
        fg, bg = theme.RATE_COLORS.get(
            group, (theme.TEXT_SECONDARY, theme.BG_ELEVATED))
        return cls(theme.RATE_LABELS.get(group, group.upper()), fg, bg, parent)


class StatusChip(QWidget):
    """Dot + label, for connection/session state."""

    def __init__(self, text: str, colour: str = theme.OK,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.setSpacing(7)
        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color:{colour}; font-size:9px;")
        self.label = QLabel(text)
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self.setStyleSheet(
            f"background:{theme.BG_ELEVATED}; border:1px solid {theme.BORDER};"
            f"border-radius:999px;")
        self.setSizePolicy(QSizePolicy.Policy.Maximum,
                           QSizePolicy.Policy.Maximum)

    def set_state(self, text: str, colour: str) -> None:
        self.label.setText(text)
        self.label.setToolTip(text)
        self.dot.setStyleSheet(f"color:{colour}; font-size:9px;")

    def set_elide_width(self, width: int) -> None:
        """Constrain the label so a long status elides instead of clipping."""
        self.label.setMaximumWidth(width)
        self.label.setTextFormat(Qt.TextFormat.PlainText)


class Panel(QFrame):
    """Titled card; `body` is the layout callers fill."""

    def __init__(self, title: str, subtitle: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(14, 9, 14, 9)
        self.title = QLabel(title)
        self.title.setObjectName("PanelHeader")
        header.addWidget(self.title)
        header.addStretch(1)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("Muted")
        self.subtitle.setStyleSheet(
            f"font-family:{theme.FONT_MONO_STACK}; font-size:10px;")
        header.addWidget(self.subtitle)
        outer.addLayout(header)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"background:{theme.BORDER_SOFT}; max-height:1px;")
        outer.addWidget(line)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(10)
        outer.addLayout(self.body, 1)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)


class MetricTile(Panel):
    """Label / big value / sub-caption, for the telemetry row (BLD-003)."""

    def __init__(self, label: str, value: str = "—", unit: str = "",
                 sub: str = "", parent: QWidget | None = None) -> None:
        super().__init__(label, parent=parent)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.value = QLabel(value)
        self.value.setStyleSheet(
            f"font-family:{theme.FONT_MONO_STACK}; font-size:24px;"
            f"font-weight:600; color:{theme.TEXT_PRIMARY};")
        row.addWidget(self.value)
        self.unit = QLabel(unit)
        self.unit.setStyleSheet(
            f"font-family:{theme.FONT_MONO_STACK}; font-size:12px;"
            f"color:{theme.TEXT_SECONDARY};")
        row.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        self.body.addLayout(row)

        self.sub = QLabel(sub)
        self.sub.setObjectName("Muted")
        self.sub.setStyleSheet("font-size:11px;")
        self.sub.setWordWrap(True)
        self.body.addWidget(self.sub)

    def set_value(self, value: str, colour: str = theme.TEXT_PRIMARY) -> None:
        self.value.setText(value)
        self.value.setStyleSheet(
            f"font-family:{theme.FONT_MONO_STACK}; font-size:24px;"
            f"font-weight:600; color:{colour};")


class UsageBar(QWidget):
    """Labelled proportion bar for the sizing report (BLD-004)."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        top = QHBoxLayout()
        self.label = QLabel(label)
        self.label.setStyleSheet("font-weight:500;")
        top.addWidget(self.label)
        top.addStretch(1)
        self.value = QLabel("—")
        self.value.setStyleSheet(
            f"font-family:{theme.FONT_MONO_STACK}; font-size:11px;"
            f"color:{theme.TEXT_SECONDARY};")
        top.addWidget(self.value)
        layout.addLayout(top)

        self.track = QFrame()
        self.track.setFixedHeight(7)
        self.track.setStyleSheet(
            f"background:{theme.BG_ELEVATED}; border-radius:4px;")
        track_layout = QHBoxLayout(self.track)
        track_layout.setContentsMargins(0, 0, 0, 0)
        self.fill = QFrame()
        self.fill.setStyleSheet(
            f"background:{theme.ACCENT}; border-radius:4px;")
        track_layout.addWidget(self.fill)
        self.spacer = QWidget()
        track_layout.addWidget(self.spacer)
        layout.addWidget(self.track)

        self.sub = QLabel("")
        self.sub.setObjectName("Muted")
        self.sub.setStyleSheet(
            f"font-family:{theme.FONT_MONO_STACK}; font-size:9px;")
        layout.addWidget(self.sub)

    def set_usage(self, used: int, limit: int, sub: str = "") -> None:
        fraction = 0.0 if limit <= 0 else min(1.0, used / limit)
        # Integer stretch keeps the fill honest at any widget width.
        self.fill.parentWidget().layout().setStretch(0, int(fraction * 1000))
        self.fill.parentWidget().layout().setStretch(1, int(1000 - fraction * 1000))
        colour = (theme.ERR if fraction > 0.95 else
                  theme.WARN if fraction > 0.85 else theme.ACCENT)
        self.fill.setStyleSheet(f"background:{colour}; border-radius:4px;")
        self.value.setText(f"{used / 1024:.1f} / {limit / 1024:.0f} kB")
        self.sub.setText(sub)
