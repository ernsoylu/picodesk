"""Main window shell.

MATLAB and CMake work must run off the GUI thread so the UI stays responsive
while the persistent engine session (GUI-002) and builds (GUI-005) stream output.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QMainWindow


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PicoDesk")
        # TODO: 3-pane routing matrix with type filtering, single-writer locks,
        # rate-transition badges, and auto-resolve wizard (GUI-007..GUI-011).
        # TODO: diagnostic console with regex-parsed, hyperlinked errors (GUI-005).
        # TODO: XCP live-tuning dashboard (GUI-012) and telemetry panel
        # (ISR utilization, overruns, seqlock faults, stack high-water marks;
        # BLD-003/BLD-005).
