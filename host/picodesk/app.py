"""Application entry point.

Startup order matters: the dependency check (GUI-004) runs while the window
is being built, so builds are disabled before the user can reach them.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from PyQt6.QtWidgets import QApplication

    from picodesk.gui import theme
    from picodesk.gui.main_window import MainWindow

    args = argv if argv is not None else sys.argv[1:]
    app = QApplication(sys.argv[:1])
    app.setApplicationName("PicoDesk")
    app.setStyleSheet(theme.STYLESHEET)

    window = MainWindow()
    for arg in args:
        path = Path(arg)
        if path.suffix == ".pdws" and path.is_file():
            window.open_workspace(path)
        elif path.name.endswith(".json") and path.is_file():
            window.load_descriptor_file(path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
