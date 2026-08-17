# XCPlite integration

Vendor Vector XCPlite v5.x+ into this directory (SRS §8). It is Apache 2.0 —
the license terms and headers **must be retained in source**.

Local additions:

- `xcp_cdc_transport.c` — custom transport shim binding XCPlite to USB CDC-ACM
  (CAL-001). Runs entirely in the Core 1 XCP task at the lowest FreeRTOS
  priority (BLD-005); DAQ data arrives via the SRAM2 ring (`rte_daq_ring.h`,
  RTE-005), never by direct fast-path access.

Throughput target: DAQ list of ≥50 signals at 100 Hz. XCP seed/key security is
out of scope for v1.0 (lab use only).
