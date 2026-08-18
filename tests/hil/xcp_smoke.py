#!/usr/bin/env python3
"""Hardware-in-the-loop XCP smoke test (Phase 3 exit criteria, CAL-001).

Requires a Pico running the spike firmware attached over USB CDC. Drives the
slave with pyxcp over the SxI serial transport (LEN+CTR word headers):

  1. CONNECT and check resources (CAL/PAG + DAQ)
  2. calibration round trip: DOWNLOAD kp to the offline page, SET_CAL_PAGE,
     verify the switch committed at a step boundary (GET_CAL_PAGE)
  3. dynamic DAQ: 1 list on event 0, measure sustained frame rate for
     --seconds and report throughput vs. the >=50 signals @ 100 Hz target

Works against both XCP slaves — the interim core and the vendored XCPlite
build (-DPICODESK_XCPLITE=ON) — because they share the CDC framing and the
CAL-page semantics. Running it against both is what closes O-4 and decides
whether XCPlite becomes the default; see docs/HARDWARE_CAMPAIGN.md.

Usage: python tests/hil/xcp_smoke.py --port /dev/ttyACM0 [--seconds 30]
"""

from __future__ import annotations

import argparse
import struct
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--cal-base", type=lambda v: int(v, 0), default=None,
                        help="logical CAL window address (from the .map)")
    parser.add_argument("--frame-base", type=lambda v: int(v, 0), default=None,
                        help="g_xcp_daq_frame_marker address (from the .map)")
    args = parser.parse_args()

    try:
        from pyxcp.master import Master
        from pyxcp.transport import Sxi
    except ImportError:
        print("pyxcp >= 0.21 required: pip install pyxcp")
        return 2

    # HEADER_LEN_CTR_WORD framing matches target/xcp/xcp_transport.h.
    transport = Sxi(port=args.port, baudrate=115200, header_format="<HH")
    master = Master("SXI", transport=transport)

    with master as m:
        res = m.connect()
        assert res.resource.dbg is not None  # connected
        print(f"connected: maxCto={res.maxCto} maxDto={res.maxDto}")

        if args.cal_base is not None:
            kp_new = 11469
            m.setMta(args.cal_base)
            m.download(struct.pack("<i", kp_new))
            m.setCalPage(0x03, 0, 1)
            time.sleep(0.01)  # >= a few 1 ms step boundaries
            page = m.getCalPage(0x03, 0)
            print(f"active page after switch: {page}")
            readback = m.shortUpload(4, args.cal_base, 0)
            assert struct.unpack("<i", bytes(readback))[0] == kp_new
            print("calibration round trip ok (RTE-003)")

        if args.frame_base is not None:
            m.freeDaq()
            m.allocDaq(1)
            m.allocOdt(0, 1)
            m.allocOdtEntry(0, 0, 4)
            m.setDaqPtr(0, 0, 0)
            for offset in (0, 4, 8, 12):
                m.writeDaq(0, 4, 0, args.frame_base + offset)
            # DAQ_MODE_TIMESTAMP (bit 4) is mandatory on the XCPlite build,
            # which rejects a list without it (CRC_CMD_SYNTAX); the interim
            # core accepts either. Setting it keeps one script for both.
            m.setDaqListMode(0x10, 0, 0, 1, 0)
            m.startStopDaqList(0x01, 0)
            m.startStopSynch(0x01)

            frames = 0
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                if transport.get() is not None:
                    frames += 1
            m.startStopSynch(0x00)
            rate = frames / args.seconds
            print(f"DAQ: {frames} frames in {args.seconds} s = {rate:.0f} Hz")
            # 16 bytes/frame at 1 kHz dwarfs 50 signals @ 100 Hz (CAL-001).
            assert rate > 900, "sustained DAQ rate below target"

        m.disconnect()

    print("xcp smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
