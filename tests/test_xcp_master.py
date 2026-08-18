"""XCP master orchestration for the Live Tuning panel (GUI-012, RTE-003).

The fake backend below is not a convenience — it implements the *actual*
calibration semantics the target has: a two-page CAL window where writes land
on the offline page and only become visible when a switch commits. That is what
makes these tests meaningful, because the property under test is that the
master never reports an edit as live before the target says the page swapped.

What is NOT covered here, deliberately: the pyxcp-over-serial leg in
`PyxcpBackend`. Faking a serial slave convincingly would test the fake. That
leg is exercised on hardware by `tests/hil/xcp_smoke.py` (the O-4 gate in
docs/HARDWARE_CAMPAIGN.md), and `PyxcpBackend` is kept thin so there is little
in it to go wrong untested.
"""

from __future__ import annotations

import struct

import pytest
from picodesk.xcp.master import (
    DaqSignal,
    Parameter,
    XcpError,
    XcpMaster,
)

CAL_BASE = 0x2002_0000
PARAMS = [
    Parameter("kp_q15", CAL_BASE + 0, "i32"),
    Parameter("ki_q15", CAL_BASE + 4, "i32"),
    Parameter("trq_limit", CAL_BASE + 8, "i32"),
]
FRAME_BASE = 0x2002_1000


class FakeSlave:
    """In-process stand-in with the target's real CAL-page behaviour.

    Two pages; the active one is what the control loop reads. Every access
    inside the CAL window is redirected to the OFFLINE page, exactly as
    ApplXcpGetPointer does on the target. A switch is only *armed* by
    SET_CAL_PAGE — `commit()` stands in for core 0 reaching a step boundary.
    """

    def __init__(self) -> None:
        self.pages = [bytearray(12), bytearray(12)]
        defaults = struct.pack("<iii", 9830, 655, 32767)
        self.pages[0][:] = defaults
        self.pages[1][:] = defaults
        self.active = 0
        self.switch_pending: int | None = None
        self.connected = False
        self.daq_signals: list[DaqSignal] = []
        self.daq_running = False
        self.queued: list[tuple[int, bytes]] = []
        self.freed = 0

    # the RTE's own commit, on the target's schedule
    def commit(self) -> None:
        if self.switch_pending is not None:
            self.active = self.switch_pending
            self.switch_pending = None

    def _offline(self) -> bytearray:
        return self.pages[self.active ^ 1]

    # --- XcpBackend -------------------------------------------------------

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def upload(self, address: int, length: int) -> bytes:
        offset = address - CAL_BASE
        return bytes(self._offline()[offset:offset + length])

    def download(self, address: int, data: bytes) -> None:
        offset = address - CAL_BASE
        self._offline()[offset:offset + len(data)] = data

    def set_cal_page(self, segment: int, page: int) -> None:
        assert segment == 0
        self.switch_pending = page  # armed, not applied

    def get_cal_page(self, segment: int) -> int:
        return self.active

    def setup_daq(self, signals, event: int) -> None:
        self.freed += 1
        self.daq_signals = list(signals)
        self.event = event

    def start_daq(self) -> None:
        self.daq_running = True

    def stop_daq(self) -> None:
        self.daq_running = False

    def poll_frames(self) -> list[tuple[int, bytes]]:
        out, self.queued = self.queued, []
        return out

    # --- test helper ------------------------------------------------------

    def push_frame(self, timestamp: int, *values: int) -> None:
        payload = b"".join(
            struct.pack(s.fmt, v) for s, v in zip(self.daq_signals, values))
        self.queued.append((timestamp, payload))

    def live(self, name: str) -> int:
        offset = {"kp_q15": 0, "ki_q15": 4, "trq_limit": 8}[name]
        return struct.unpack_from("<i", self.pages[self.active], offset)[0]


@pytest.fixture
def slave() -> FakeSlave:
    return FakeSlave()


@pytest.fixture
def master(slave: FakeSlave) -> XcpMaster:
    m = XcpMaster(slave)
    m.load_parameters(PARAMS)
    m.connect()
    return m


# --- session --------------------------------------------------------------


def test_connect_and_disconnect_track_state(slave: FakeSlave) -> None:
    m = XcpMaster(slave)
    assert not m.connected
    m.connect()
    assert m.connected and slave.connected
    m.connect()  # idempotent
    m.disconnect()
    assert not m.connected and not slave.connected


def test_operations_before_connect_are_refused(slave: FakeSlave) -> None:
    m = XcpMaster(slave)
    m.load_parameters(PARAMS)
    with pytest.raises(XcpError, match="not connected"):
        m.read_parameter("kp_q15")


def test_disconnect_stops_a_running_daq(master: XcpMaster, slave: FakeSlave) -> None:
    """Leaving DAQ running on the target after the panel disconnects would
    keep the slave streaming into nothing."""
    master.configure_daq([DaqSignal("torque", FRAME_BASE, "i32")])
    master.start_daq()
    master.disconnect()
    assert not slave.daq_running
    assert not master.daq_running


# --- calibration is transactional (RTE-003) -------------------------------


def test_download_does_not_go_live_until_the_switch_commits(
        master: XcpMaster, slave: FakeSlave) -> None:
    assert slave.live("kp_q15") == 9830

    master.write_parameter("kp_q15", 11469)
    assert slave.live("kp_q15") == 9830, "edit became live before the switch"
    assert master.read_parameter("kp_q15") == 11469, "offline read-back wrong"

    master.request_page_switch()
    assert slave.live("kp_q15") == 9830, "switch applied before the boundary"

    slave.commit()  # core 0, at a model_step() boundary
    assert slave.live("kp_q15") == 11469


def test_multi_parameter_edit_lands_atomically(
        master: XcpMaster, slave: FakeSlave) -> None:
    """The whole point of the page swap: several parameters become visible in
    the same step, never half of them."""
    master.write_parameter("kp_q15", 1)
    master.write_parameter("ki_q15", 2)
    master.write_parameter("trq_limit", 3)
    assert (slave.live("kp_q15"), slave.live("ki_q15"), slave.live("trq_limit")) \
        == (9830, 655, 32767)

    master.request_page_switch()
    slave.commit()
    assert (slave.live("kp_q15"), slave.live("ki_q15"), slave.live("trq_limit")) \
        == (1, 2, 3)


def test_switch_committed_reports_the_targets_schedule(
        master: XcpMaster, slave: FakeSlave) -> None:
    master.write_parameter("kp_q15", 42)
    target_page = master.active_page() ^ 1
    master.request_page_switch()
    assert not master.switch_committed(target_page)
    slave.commit()
    assert master.switch_committed(target_page)


def test_read_all_returns_every_parameter(master: XcpMaster) -> None:
    assert master.read_all() == {"kp_q15": 9830, "ki_q15": 655, "trq_limit": 32767}


def test_unknown_parameter_is_rejected(master: XcpMaster) -> None:
    with pytest.raises(XcpError, match="unknown parameter"):
        master.write_parameter("nope", 1)


def test_out_of_range_value_is_rejected_before_the_wire(
        master: XcpMaster, slave: FakeSlave) -> None:
    """Better a clear error than a silently truncated calibration value."""
    with pytest.raises(XcpError, match="does not fit"):
        master.write_parameter("kp_q15", 2**40)
    assert master.read_parameter("kp_q15") == 9830


def test_unsupported_type_is_rejected() -> None:
    with pytest.raises(XcpError, match="unsupported type"):
        _ = Parameter("x", 0, "i24").fmt


def test_short_upload_is_an_error_not_a_wrong_value(
        master: XcpMaster, slave: FakeSlave) -> None:
    slave.upload = lambda address, length: b"\x01\x02"  # type: ignore[assignment]
    with pytest.raises(XcpError, match="expected 4"):
        master.read_parameter("kp_q15")


# --- measurement (RTE-005) ------------------------------------------------


SIGNALS = [
    DaqSignal("torque_cmd", FRAME_BASE + 4, "i32"),
    DaqSignal("speed_est", FRAME_BASE + 12, "i32"),
]


def test_daq_frames_unpack_in_signal_order(master: XcpMaster, slave: FakeSlave) -> None:
    master.configure_daq(SIGNALS)
    master.start_daq()
    slave.push_frame(1000, -1234, 4321)
    slave.push_frame(2000, -1235, 4322)

    assert master.poll() == [(1000, (-1234, 4321)), (2000, (-1235, 4322))]
    assert master.poll() == [], "frames must be drained, not replayed"


def test_mixed_width_signals_unpack_at_the_right_offsets(
        master: XcpMaster, slave: FakeSlave) -> None:
    """A wrong offset here silently reports one signal's bytes as another's."""
    signals = [
        DaqSignal("tick", FRAME_BASE, "u16"),
        DaqSignal("torque", FRAME_BASE + 2, "i32"),
        DaqSignal("flags", FRAME_BASE + 6, "u8"),
    ]
    master.configure_daq(signals)
    master.start_daq()
    slave.push_frame(500, 7, -9, 3)
    assert master.poll() == [(500, (7, -9, 3))]
    assert master.frame_size == 7


def test_torn_frame_is_dropped_not_decoded(master: XcpMaster, slave: FakeSlave) -> None:
    """RTE-005 says a torn frame should be impossible. If one arrives anyway,
    silently unpacking misaligned bytes into plausible values is the worst
    available outcome."""
    master.configure_daq(SIGNALS)
    master.start_daq()
    slave.queued.append((1000, b"\x01\x02\x03"))  # short
    slave.push_frame(2000, 5, 6)
    assert master.poll() == [(2000, (5, 6))]


def test_start_without_configure_is_refused(master: XcpMaster) -> None:
    with pytest.raises(XcpError, match="configure_daq"):
        master.start_daq()


def test_empty_or_duplicate_daq_lists_are_refused(master: XcpMaster) -> None:
    with pytest.raises(XcpError, match="at least one signal"):
        master.configure_daq([])
    with pytest.raises(XcpError, match="duplicate"):
        master.configure_daq([SIGNALS[0], SIGNALS[0]])


def test_stop_is_idempotent(master: XcpMaster, slave: FakeSlave) -> None:
    master.configure_daq(SIGNALS)
    master.start_daq()
    master.stop_daq()
    master.stop_daq()
    assert not slave.daq_running
