"""Generate the RTE C sources from the model descriptor and routing config.

Emits: the Core 0 dispatch ISR and Core 1 rate-group tasks with
vTaskNotifyGiveFromISR release (RTE-002), copy-in shadow buffers (RTE-001),
seqlock instantiations for >32-bit cross-core signals (RTE-004), CAL page
tables (RTE-003), DAQ ring wiring (RTE-005), and the task priority/stack
table (BLD-005). Every fast-path emission carries __not_in_flash_func()
(BLD-001), and all symbols are per-model prefixed (MAT-003).
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "templates"


def generate_rte(descriptor: dict, routing: dict, out_dir: Path) -> None:
    raise NotImplementedError
