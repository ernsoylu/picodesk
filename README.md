# PicoDesk

Python/MATLAB toolchain that turns a batch of independent Simulink models into deterministic
dual-core RP2040 firmware: a PyQt6 GUI routes model signals over a Virtual Functional Bus,
generates a multi-rate RTE in C, builds with the Pico SDK, and performs live XCP calibration
and DAQ over USB CDC.

- **Spec:** [REQUIREMENTS.md](REQUIREMENTS.md) (SRS v7.1) — every change traces to a requirement ID.
- **Contributor guidance:** [CLAUDE.md](CLAUDE.md) — architecture, invariants, conventions.

## Layout

- `host/` — Python toolchain (GUI, MATLAB bridge, RTE generator, build orchestration, XCP master).
  Install for development with `pip install -e "host[dev]"` (Python 3.9–3.12).
- `target/` — C firmware: RTE runtime, default HAL, XCPlite integration, linker scripts.
  Built via CMake + Pico SDK (driven by the host toolchain).
- `tests/` — host-side test suite (`pytest`).

## Licence

PicoDesk is **GPL-3.0-or-later** ([LICENSE](LICENSE)). The GUI uses PyQt6 under
its GPL option, so the toolchain as a whole carries those terms: distributing a
build means offering recipients the Corresponding Source. Running it internally
is not distribution and carries no obligation.

The firmware it produces is **not** covered by that. The `.uf2` links no Qt —
only the Pico SDK (BSD-3-Clause), FreeRTOS and TinyUSB (MIT), Vector XCPlite
(MIT), and your own generated ERT code — so your firmware is yours.
[docs/THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md) has the full
inventory.
