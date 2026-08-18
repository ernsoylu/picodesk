"""Background workers (GUI-002, GUI-005).

Every long operation — MATLAB extraction, RTE generation, CMake builds —
runs on a QThreadPool worker so the UI thread never blocks. Output arrives
as signals: `line` per stdout line for the console, `finished` with the
result, `failed` with a message.

The freeze budget in the Phase 7 exit criteria (no UI stall > 100 ms) is a
direct consequence of this: nothing here touches widgets, and the GUI only
ever reacts to signals.
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal


class WorkerSignals(QObject):
    line = pyqtSignal(str)
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class Worker(QRunnable):
    """Runs `fn(emit_line, **kwargs)` off the UI thread.

    `fn` receives a line-emitting callable as its first argument so build
    output streams to the console live rather than arriving in one lump.
    """

    def __init__(self, fn: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:  # pragma: no cover - exercised via the pool
        try:
            result = self.fn(self.signals.line.emit, **self.kwargs)
        except Exception:  # noqa: BLE001 — a worker must never kill the UI thread
            self.signals.failed.emit(traceback.format_exc(limit=6))
        else:
            self.signals.finished.emit(result)


def submit(fn: Callable[..., Any], *, on_line: Callable[[str], None] | None = None,
           on_finished: Callable[[Any], None] | None = None,
           on_failed: Callable[[str], None] | None = None,
           pool: QThreadPool | None = None, **kwargs: Any) -> Worker:
    """Queue a worker and wire its signals in one call."""
    worker = Worker(fn, **kwargs)
    if on_line is not None:
        worker.signals.line.connect(on_line)
    if on_finished is not None:
        worker.signals.finished.connect(on_finished)
    if on_failed is not None:
        worker.signals.failed.connect(on_failed)
    (pool or QThreadPool.globalInstance()).start(worker)
    return worker
