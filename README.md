# PicoDesk

Python/MATLAB toolchain that turns a batch of independent Simulink models into deterministic
dual-core RP2040 firmware: a PyQt6 GUI routes model signals over a Virtual Functional Bus,
generates a multi-rate RTE in C, builds with the Pico SDK, and performs live XCP calibration
and DAQ over USB CDC.

- **Spec:** [REQUIREMENTS.md](REQUIREMENTS.md) (SRS v7.0) — every change traces to a requirement ID.
- **Contributor guidance:** [CLAUDE.md](CLAUDE.md) — architecture, invariants, conventions.

## Layout

- `host/` — Python toolchain (GUI, MATLAB bridge, RTE generator, build orchestration, XCP master).
  Install for development with `pip install -e "host[dev]"` (Python 3.9–3.11).
- `target/` — C firmware: RTE runtime, default HAL, XCPlite integration, linker scripts.
  Built via CMake + Pico SDK (driven by the host toolchain).
- `tests/` — host-side test suite (`pytest`).
