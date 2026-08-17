"""Static SRAM/Flash footprint estimation prior to CMake (BLD-004).

The build halts when estimates exceed the ceilings below; the GUI displays
the full report either way.
"""

from __future__ import annotations

SRAM_LIMIT_BYTES = 200 * 1024
FLASH_LIMIT_BYTES = 1536 * 1024


def estimate_footprint(descriptor: dict, routing: dict) -> dict:
    """Return {"sram_bytes": ..., "flash_bytes": ..., "per_model": {...}}."""
    raise NotImplementedError
