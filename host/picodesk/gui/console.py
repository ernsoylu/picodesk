"""Diagnostic console (GUI-005).

Streams stdout/stderr from MATLAB, the RTE generator and CMake, colour-codes
errors and warnings, and turns `file:line` diagnostics into clickable links
that emit `locationActivated(file, line)` for the host to open.

Parsing is shared with the headless build driver (buildsys.cmake_driver), so
what the console highlights and what CI reports can never drift apart.
"""

from __future__ import annotations

import html
from urllib.parse import quote

from PyQt6.QtCore import QUrl, QUrlQuery, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from picodesk.buildsys.cmake_driver import Diagnostic, parse_diagnostic
from picodesk.gui import theme

MAX_LINES = 5000


class ConsoleModel:
    """Qt-free half: keeps lines + parsed diagnostics, applies the filter."""

    def __init__(self, max_lines: int = MAX_LINES) -> None:
        self.max_lines = max_lines
        self.lines: list[tuple[str, Diagnostic | None]] = []

    def append(self, line: str) -> Diagnostic | None:
        diagnostic = parse_diagnostic(line)
        self.lines.append((line, diagnostic))
        if len(self.lines) > self.max_lines:
            del self.lines[: len(self.lines) - self.max_lines]
        return diagnostic

    def clear(self) -> None:
        self.lines.clear()

    def counts(self) -> dict[str, int]:
        errors = sum(1 for _, d in self.lines if d and d.severity == "error")
        warnings = sum(1 for _, d in self.lines if d and d.severity == "warning")
        return {"errors": errors, "warnings": warnings,
                "lines": len(self.lines)}

    def filtered(self, level: str) -> list[tuple[str, Diagnostic | None]]:
        if level == "All":
            return list(self.lines)
        if level == "Errors":
            return [(t, d) for t, d in self.lines
                    if d and d.severity == "error"]
        if level == "Warnings+":
            return [(t, d) for t, d in self.lines
                    if d and d.severity in ("error", "warning")]
        return list(self.lines)


class DiagnosticConsole(QWidget):
    locationActivated = pyqtSignal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = ConsoleModel()
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        title = QLabel("DIAGNOSTIC CONSOLE")
        title.setObjectName("PanelHeader")
        bar.addWidget(title)
        self.summary = QLabel("0 errors · 0 warnings")
        self.summary.setObjectName("Muted")
        bar.addWidget(self.summary)
        bar.addStretch(1)
        self.filter_box = QComboBox()
        self.filter_box.addItems(["All", "Warnings+", "Errors"])
        self.filter_box.currentTextChanged.connect(lambda _: self._rerender())
        bar.addWidget(self.filter_box)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        bar.addWidget(clear)
        layout.addLayout(bar)

        self.view = QTextBrowser()
        self.view.setObjectName("Console")
        self.view.setReadOnly(True)
        self.view.setOpenLinks(False)  # we route clicks ourselves
        self.view.setOpenExternalLinks(False)
        self.view.document().setMaximumBlockCount(MAX_LINES)
        self.view.anchorClicked.connect(self._on_anchor)
        layout.addWidget(self.view, 1)

    # -- API ----------------------------------------------------------------

    def append_line(self, line: str) -> None:
        diagnostic = self.model.append(line)
        if self._passes_filter(diagnostic):
            self._render_line(line, diagnostic)
        self._update_summary()

    def append_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.append_line(line)

    def clear(self) -> None:
        self.model.clear()
        self.view.clear()
        self._update_summary()

    # -- internals ----------------------------------------------------------

    def _passes_filter(self, diagnostic: Diagnostic | None) -> bool:
        level = self.filter_box.currentText()
        if level == "All":
            return True
        if level == "Errors":
            return bool(diagnostic and diagnostic.severity == "error")
        return bool(diagnostic and diagnostic.severity in ("error", "warning"))

    def _render_line(self, line: str, diagnostic: Diagnostic | None) -> None:
        colour = (theme.severity_color(diagnostic.severity) if diagnostic
                  else theme.TEXT_SECONDARY)
        escaped = html.escape(line)
        if diagnostic and diagnostic.file and diagnostic.line:
            where = html.escape(f"{diagnostic.file}:{diagnostic.line}")
            # The path travels in the query, not the host: QUrl lowercases
            # host components, which would corrupt case-sensitive paths.
            target = (f"picodesk://open?file={quote(diagnostic.file, safe='')}"
                      f"&line={diagnostic.line}")
            link = (f'<a href="{target}" '
                    f'style="color:{theme.INFO};">{where}</a>')
            escaped = escaped.replace(where, link, 1)
        self.view.append(
            f'<span style="color:{colour};white-space:pre">{escaped}</span>')
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    def _rerender(self) -> None:
        self.view.clear()
        for line, diagnostic in self.model.filtered(
                self.filter_box.currentText()):
            self._render_line(line, diagnostic)

    def _on_anchor(self, url) -> None:
        """picodesk://open?file=<path>&line=<n> — for the host to open."""
        if url.scheme() != "picodesk":
            return
        query = QUrlQuery(url)
        path = query.queryItemValue("file", QUrl.ComponentFormattingOption.FullyDecoded)
        try:
            line = int(query.queryItemValue("line"))
        except ValueError:
            line = 0
        if path:
            self.locationActivated.emit(path, line)

    def _update_summary(self) -> None:
        counts = self.model.counts()
        colour = (theme.ERR if counts["errors"] else
                  theme.WARN if counts["warnings"] else theme.TEXT_MUTED)
        self.summary.setText(
            f"<span style='color:{colour}'>{counts['errors']} errors · "
            f"{counts['warnings']} warnings</span> · {counts['lines']} lines")
