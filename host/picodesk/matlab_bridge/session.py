"""Persistent MATLAB engine session with crash recovery (GUI-002).

`MatlabEngineSession` owns one long-lived matlab.engine instance. Every call
goes through `call()`, which detects a dead engine, restarts it once, replays
the MATLAB-side path setup, and retries — so a MATLAB crash costs one restart
instead of a failed workspace operation.

matlabengine is imported lazily and is not a declared dependency: its version
is locked to the locally installed MATLAB release (R2025b, SRS section 8
as amended in v7.1). Everything above the `EngineProtocol` seam is testable without
MATLAB (tests/test_matlab_bridge.py uses a fake engine).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Protocol

#: MATLAB releases whose Python engine bindings support which interpreters.
#: (SRS section 8: Python must align exactly with the MATLAB release.)
SUPPORTED_PYTHON = {
    # R2025b is the pinned release (SRS v7.1). The older entries stay so a
    # site still on them gets a precise message rather than "unknown".
    "R2023b": ((3, 9), (3, 10), (3, 11)),
    "R2024a": ((3, 9), (3, 10), (3, 11)),
    "R2024b": ((3, 9), (3, 10), (3, 11)),
    "R2025a": ((3, 9), (3, 10), (3, 11), (3, 12)),
    "R2025b": ((3, 9), (3, 10), (3, 11), (3, 12)),
}

#: The release this project validates against (SRS section 8, v7.1).
PINNED_RELEASE = "R2025b"

#: Directory with the MATLAB-side helpers (picodesk_extract.m, ...).
MATLAB_FUNCTIONS_DIR = Path(__file__).parent / "matlab"


class MatlabSessionError(RuntimeError):
    """The engine is unavailable and could not be recovered."""


class MatlabCallError(RuntimeError):
    """The MATLAB code raised — the engine itself is alive (G-9).

    Callers treat this as data about the call's subject (a broken model, a
    bad argument), never as a session failure: restarting a healthy engine
    costs a cold start and, in a batch, used to abort every remaining model.
    """


class EngineProtocol(Protocol):
    """The slice of matlab.engine the session uses (seam for tests)."""

    def feval(self, name: str, *args: Any, nargout: int = 1) -> Any: ...

    def quit(self) -> None: ...


class MatlabEngineSession:
    """One persistent engine; restart-once-and-retry on crash (GUI-002)."""

    def __init__(self, engine_factory: Any = None) -> None:
        # engine_factory() -> EngineProtocol; defaults to matlab.engine.
        self._engine_factory = engine_factory or _default_engine_factory
        self._engine: EngineProtocol | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._engine is None:
            self._engine = self._engine_factory()
            self._setup_paths()

    def stop(self) -> None:
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:  # noqa: BLE001, S110 — engine already gone; any engine-specific error means the same
                pass
            self._engine = None

    @property
    def alive(self) -> bool:
        return self._engine is not None

    # -- calls -------------------------------------------------------------

    def call(self, function: str, *args: Any, nargout: int = 1) -> Any:
        """feval with crash recovery: a dead engine is restarted exactly once
        and the call retried; a second failure propagates.

        A MATLAB *execution* error is not a crash — the engine is alive and
        the error describes the call's subject (e.g. a model that cannot
        load). Those surface as MatlabCallError immediately, with no restart
        (G-9); only engine-level failures take the restart path (GUI-002).
        """
        self.start()
        assert self._engine is not None
        try:
            return self._engine.feval(function, *args, nargout=nargout)
        except MatlabSessionError:
            raise
        except Exception as first_error:
            if _is_execution_error(first_error):
                raise MatlabCallError(str(first_error)) from first_error
            self._engine = None  # presume dead; cold-start replacement
            try:
                self.start()
                assert self._engine is not None
                return self._engine.feval(function, *args, nargout=nargout)
            except Exception as second_error:
                if _is_execution_error(second_error):
                    raise MatlabCallError(str(second_error)) from second_error
                raise MatlabSessionError(
                    f"MATLAB call {function!r} failed twice (engine restarted "
                    f"in between): {first_error}; then: {second_error}"
                ) from second_error

    # -- helpers -----------------------------------------------------------

    def _setup_paths(self) -> None:
        assert self._engine is not None
        self._engine.feval("addpath", str(MATLAB_FUNCTIONS_DIR), nargout=0)


def check_python_alignment(matlab_release: str) -> str | None:
    """Return a human-readable problem description, or None when this
    interpreter matches the MATLAB release's engine support (SRS section 8)."""
    supported = SUPPORTED_PYTHON.get(matlab_release)
    if supported is None:
        return (
            f"MATLAB {matlab_release} is outside the supported range "
            f"{sorted(SUPPORTED_PYTHON)} (SRS section 8)"
        )
    if sys.version_info[:2] not in supported:
        versions = ", ".join(".".join(map(str, v)) for v in supported)
        return (
            f"Python {sys.version_info.major}.{sys.version_info.minor} does "
            f"not match MATLAB {matlab_release} engine support ({versions})"
        )
    return None


def _is_execution_error(exc: Exception) -> bool:
    """MATLAB-side errors arrive as matlab.engine.MatlabExecutionError (and
    SyntaxError for malformed calls); both mean the engine is alive. Detected
    by class name so matlabengine stays an optional import and test fakes can
    raise a same-named class without depending on MATLAB."""
    return type(exc).__name__ in ("MatlabExecutionError", "MatlabSyntaxError")


def _default_engine_factory() -> EngineProtocol:
    try:
        import matlab.engine
    except ImportError as exc:
        raise MatlabSessionError(
            "matlabengine is not installed; install the package matching the "
            f"local MATLAB release (pinned: {PINNED_RELEASE})"
        ) from exc
    return matlab.engine.start_matlab()
