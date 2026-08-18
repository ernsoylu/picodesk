"""XCP master for the Live Tuning panel (GUI-012, CAL-001).

Calibration follows the target's transactional model exactly (RTE-003): a
DOWNLOAD into the CAL window is redirected by the slave to the OFFLINE page and
stays invisible to the running loop, and only SET_CAL_PAGE arms the swap that
core 0 commits at a `model_step()` boundary. The master never pretends an edit
is live before the target says the page switched.

Structure, and why. Everything that needs a wire is behind `XcpBackend`:

    XcpMaster  ->  XcpBackend  ->  PyxcpBackend -> pyxcp -> serial -> CDC

`XcpMaster` holds the parameter map, the struct packing, the DAQ list
bookkeeping and the page handshake — all of which are testable in-process
against a fake backend, and all of which are where the bugs live.
`PyxcpBackend` is deliberately thin, because the one thing that genuinely
cannot be tested without a board is the serial leg; that is `tests/hil/
xcp_smoke.py` and the O-4 gate, not something to fake convincingly here.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

# XCP addresses are target pointers and the slave's base address is 0
# (target/xcplite/port/picodesk_xcp_appl.c), so A2L addresses from DWARF are
# usable directly with no translation table to drift out of date.
XCP_ADDR_EXT = 0

# SET_CAL_PAGE / GET_CAL_PAGE mode bits: both ECU and XCP access, which is how
# the slave reports a single logical segment.
CAL_PAGE_MODE_ALL = 0x03

# DAQ_MODE_TIMESTAMP. XCPlite rejects a DAQ list without it (CRC_CMD_SYNTAX);
# the interim core accepts either, so setting it always keeps one master
# working against both slaves.
DAQ_MODE_TIMESTAMP = 0x10

_STRUCT_FMT = {
    "u8": "<B", "i8": "<b",
    "u16": "<H", "i16": "<h",
    "u32": "<I", "i32": "<i",
    "f32": "<f", "f64": "<d",
}


class XcpError(Exception):
    """The master could not carry out a request."""


@dataclass(frozen=True)
class Parameter:
    """One calibratable value, located by the DWARF-derived A2L (CAL-002)."""

    name: str
    address: int
    dtype: str = "i32"

    @property
    def fmt(self) -> str:
        try:
            return _STRUCT_FMT[self.dtype]
        except KeyError:
            raise XcpError(f"{self.name}: unsupported type {self.dtype!r}") from None

    @property
    def size(self) -> int:
        return struct.calcsize(self.fmt)


@dataclass(frozen=True)
class DaqSignal:
    """One signal in the DAQ list, addressed inside the coherent frame."""

    name: str
    address: int
    dtype: str = "i32"

    @property
    def fmt(self) -> str:
        return Parameter(self.name, self.address, self.dtype).fmt

    @property
    def size(self) -> int:
        return struct.calcsize(self.fmt)


class XcpBackend(Protocol):
    """The wire operations the panel needs. Kept small on purpose."""

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def upload(self, address: int, length: int) -> bytes: ...
    def download(self, address: int, data: bytes) -> None: ...
    def set_cal_page(self, segment: int, page: int) -> None: ...
    def get_cal_page(self, segment: int) -> int: ...
    def setup_daq(self, signals: Sequence[DaqSignal], event: int) -> None: ...
    def start_daq(self) -> None: ...
    def stop_daq(self) -> None: ...
    def poll_frames(self) -> list[tuple[int, bytes]]: ...


class XcpMaster:
    """Drives a PicoDesk slave for calibration and measurement."""

    def __init__(self, backend: XcpBackend) -> None:
        self.backend = backend
        self._connected = False
        self._parameters: dict[str, Parameter] = {}
        self._daq_signals: tuple[DaqSignal, ...] = ()
        self._daq_running = False

    # --- session ----------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def daq_running(self) -> bool:
        return self._daq_running

    def connect(self) -> None:
        if self._connected:
            return
        self.backend.connect()
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            return
        if self._daq_running:
            self.stop_daq()
        self.backend.disconnect()
        self._connected = False

    def _require_connection(self) -> None:
        if not self._connected:
            raise XcpError("not connected")

    # --- calibration (RTE-003) --------------------------------------------

    def load_parameters(self, parameters: Sequence[Parameter]) -> None:
        self._parameters = {p.name: p for p in parameters}

    def _parameter(self, name: str) -> Parameter:
        try:
            return self._parameters[name]
        except KeyError:
            raise XcpError(f"unknown parameter {name!r}") from None

    def read_parameter(self, name: str) -> float | int:
        self._require_connection()
        p = self._parameter(name)
        raw = self.backend.upload(p.address, p.size)
        if len(raw) != p.size:
            raise XcpError(
                f"{name}: slave returned {len(raw)} bytes, expected {p.size}")
        return struct.unpack(p.fmt, raw)[0]

    def read_all(self) -> dict[str, float | int]:
        return {name: self.read_parameter(name) for name in self._parameters}

    def write_parameter(self, name: str, value: float) -> None:
        """DOWNLOAD into the CAL window.

        The slave redirects this to the offline page, so the value does NOT
        become live here — `request_page_switch()` is what makes it so.
        """
        self._require_connection()
        p = self._parameter(name)
        try:
            data = struct.pack(p.fmt, value)
        except struct.error as exc:
            raise XcpError(f"{name}: {value!r} does not fit {p.dtype}: {exc}") from exc
        self.backend.download(p.address, data)

    def request_page_switch(self) -> None:
        """Arm SET_CAL_PAGE. Core 0 commits at the next step boundary."""
        self._require_connection()
        self.backend.set_cal_page(0, self.active_page() ^ 1)

    def active_page(self) -> int:
        self._require_connection()
        return self.backend.get_cal_page(0)

    def switch_committed(self, expected_page: int) -> bool:
        """Has the target actually swapped? The panel polls this rather than
        assuming, because the commit happens on the target's schedule."""
        return self.active_page() == expected_page

    # --- measurement (RTE-005) --------------------------------------------

    def configure_daq(self, signals: Sequence[DaqSignal], event: int = 0) -> None:
        self._require_connection()
        if not signals:
            raise XcpError("a DAQ list needs at least one signal")
        names = [s.name for s in signals]
        if len(set(names)) != len(names):
            raise XcpError(f"duplicate DAQ signal names: {names}")
        self.backend.setup_daq(signals, event)
        self._daq_signals = tuple(signals)

    def start_daq(self) -> None:
        self._require_connection()
        if not self._daq_signals:
            raise XcpError("configure_daq() first")
        self.backend.start_daq()
        self._daq_running = True

    def stop_daq(self) -> None:
        if not self._daq_running:
            return
        self.backend.stop_daq()
        self._daq_running = False

    @property
    def frame_size(self) -> int:
        return sum(s.size for s in self._daq_signals)

    def poll(self) -> list[tuple[int, tuple[float | int, ...]]]:
        """Drain queued DAQ frames as (timestamp_us, values).

        A short frame is dropped rather than unpacked into misaligned values —
        a torn frame silently decoded is worse than a missing one, and RTE-005
        exists precisely so this should never happen.
        """
        self._require_connection()
        out: list[tuple[int, tuple[float | int, ...]]] = []
        for timestamp, payload in self.backend.poll_frames():
            if len(payload) < self.frame_size:
                continue
            values: list[float | int] = []
            offset = 0
            for signal in self._daq_signals:
                values.append(
                    struct.unpack_from(signal.fmt, payload, offset)[0])
                offset += signal.size
            out.append((timestamp, tuple(values)))
        return out


class PyxcpBackend:
    """`XcpBackend` over pyxcp's SxI serial transport (CAL-001).

    Deliberately thin: it translates the calls above into pyxcp ones and does
    nothing else, because this is the only layer a board is required to
    exercise. Verified by `tests/hil/xcp_smoke.py` against real hardware — the
    O-4 gate in docs/HARDWARE_CAMPAIGN.md.
    """

    def __init__(self, port: str, baudrate: int = 115200) -> None:
        self.port = port
        self.baudrate = baudrate
        self._master = None

    def connect(self) -> None:
        try:
            from pyxcp.master import Master
            from pyxcp.transport import Sxi
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise XcpError("pyxcp >= 0.21 is required for live tuning") from exc

        # HEADER_LEN_CTR_WORD framing, matching target/xcp/xcp_transport.h and
        # the XCPlite transport in target/xcplite/port/picodesk_xcp_tl.c.
        transport = Sxi(port=self.port, baudrate=self.baudrate,
                        header_format="<HH")
        self._master = Master("SXI", transport=transport)
        self._master.connect()

    def disconnect(self) -> None:
        if self._master is None:
            return
        try:
            self._master.disconnect()
        finally:
            self._master.close()
            self._master = None

    def _require(self):
        if self._master is None:
            raise XcpError("backend is not connected")
        return self._master

    def upload(self, address: int, length: int) -> bytes:
        return bytes(self._require().shortUpload(length, address, XCP_ADDR_EXT))

    def download(self, address: int, data: bytes) -> None:
        m = self._require()
        m.setMta(address, XCP_ADDR_EXT)
        m.download(data)

    def set_cal_page(self, segment: int, page: int) -> None:
        self._require().setCalPage(CAL_PAGE_MODE_ALL, segment, page)

    def get_cal_page(self, segment: int) -> int:
        return int(self._require().getCalPage(CAL_PAGE_MODE_ALL, segment))

    def setup_daq(self, signals: Sequence[DaqSignal], event: int) -> None:
        m = self._require()
        m.freeDaq()
        m.allocDaq(1)
        m.allocOdt(0, 1)
        m.allocOdtEntry(0, 0, len(signals))
        m.setDaqPtr(0, 0, 0)
        for signal in signals:
            m.writeDaq(0, signal.size, XCP_ADDR_EXT, signal.address)
        m.setDaqListMode(DAQ_MODE_TIMESTAMP, 0, event, 1, 0)

    def start_daq(self) -> None:
        m = self._require()
        m.startStopDaqList(1, 0)
        m.startStopSynch(1)

    def stop_daq(self) -> None:
        m = self._require()
        m.startStopSynch(0)

    def poll_frames(self) -> list[tuple[int, bytes]]:
        m = self._require()
        frames: list[tuple[int, bytes]] = []
        while True:
            frame = m.transport.get()
            if frame is None:
                break
            # DTO layout: PID, DAQ number, 32-bit timestamp, then the ODT data.
            payload = bytes(frame)
            if len(payload) < 6:
                continue
            timestamp = int.from_bytes(payload[2:6], "little")
            frames.append((timestamp, payload[6:]))
        return frames
