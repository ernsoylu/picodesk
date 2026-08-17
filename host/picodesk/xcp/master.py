"""pyxcp master over USB CDC-Serial for the Live Tuning panel (GUI-012, CAL-001).

Calibration writes go to the offline CAL page and are activated atomically via
SET_CAL_PAGE, matching the target's step-boundary page swap (RTE-003).
DAQ throughput target: >=50 signals at 100 Hz over CDC.
"""

from __future__ import annotations


class XcpMaster:
    def connect(self, port: str) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError
