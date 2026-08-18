#!/usr/bin/env python3
"""Render the GUI offscreen to PNGs — visual review without a display.

Populates the window from the fixture workspace so the pages show real
content, then grabs one image per page.

Usage: QT_QPA_PLATFORM=offscreen tools/gui_screenshot.py [outdir]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "host"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from picodesk.buildsys import sizing_report as sz
from picodesk.gui import theme
from picodesk.gui.main_window import MainWindow
from PyQt6.QtWidgets import QApplication

FIXTURES = REPO / "tests" / "fixtures" / "gen_ws"

DEMO_TELEMETRY = {
    "exec_max_us": 612,
    "overrun_count": 0,
    "seqlock_fault_count": 0,
    "daq_rate_hz": 1000,
    "heartbeat_delta": 1000,
}
DEMO_PARAMS = {"kp_q15": 9830.0, "ki_q15": 655.0, "trq_limit": 32767.0}


def populate(window: MainWindow) -> None:
    descriptor = json.loads((FIXTURES / "descriptor.json").read_text())
    routing = json.loads((FIXTURES / "routing.json").read_text())

    # A stale model and a MAT-002 offender make the state column meaningful.
    descriptor["models"]["ThermalCtrl"] = {
        "file": "ThermalCtrl.slx", "slx_sha256": "e" * 64,
        "base_rate_s": 0.001, "rate_group": "fast_1ms",
        "inports": [{"name": "temp_raw", "data_type": "double", "width": 1}],
        "outports": [{"name": "derate", "data_type": "int16", "width": 1}],
        "internal_types": ["double"],
    }
    window.workspace.descriptor = descriptor
    window.workspace.routing = routing
    window.models_page.set_descriptor(descriptor, stale={"SlowSense"})
    window.routing_page.load(descriptor, routing, window.hal_manifest)
    window.build_page.set_blocked_models(window.models_page.blocked_models())
    window.build_page.set_sizing(sz.estimate_footprint(descriptor, routing))
    window.build_page.set_reproducible(True, "7c1d90aa5f2b4e11")
    window.build_page.set_artifact_ready(True)
    window.models_page.set_matlab_state("MATLAB R2025b · session live", theme.OK)

    window.calibration_page.set_link_state("/dev/ttyACM0 · DAQ 1 kHz", theme.OK)
    window.calibration_page.set_parameters(DEMO_PARAMS)
    window.calibration_page.session.edit("kp_q15", 11469.0)
    window.calibration_page._render_parameters()
    window.calibration_page._update_banner()
    window.calibration_page.update_telemetry(DEMO_TELEMETRY)
    window.calibration_page.set_signals(
        ["FastCtrl.torque_cmd", "SlowSense.derate_pct", "RTE.heartbeat"],
        {"FastCtrl.torque_cmd": "fast_1ms",
         "SlowSense.derate_pct": "slow_100ms"})

    for line in (
        "[matlab] extracting FastCtrl.slx … descriptor ok (hash a91f…3c2e)",
        "[rtegen] 2 models · 2 seqlock buses · DAQ frame 8 B",
        "SRC/rte/rte_gen.c:214:9: warning: unused variable 'tmp'",
        "SRC/src/main.c:88:1: error: 'pd_Ghost_step' undeclared here",
        "[cmake] [41/59] Linking CXX executable picodesk_gen_firmware.elf",
    ):
        window.console.append_line(line)


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "build-gui-shots"
    outdir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv[:1])
    app.setStyleSheet(theme.STYLESHEET)
    window = MainWindow()
    window.resize(1440, 900)
    populate(window)
    window.show()
    app.processEvents()

    for index, (label, _key) in enumerate(
            [("models", ""), ("routing", ""), ("build", ""),
             ("calibration", "")]):
        window.pages.setCurrentIndex(index)
        app.processEvents()
        path = outdir / f"{index}_{label}.png"
        window.grab().save(str(path))
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
