"""Hardware-in-the-loop campaign support (docs/HARDWARE_CAMPAIGN.md).

The gates that emulation cannot close — NFR-1 dispatch jitter, NFR-3
critical-section hold time — are measured with a logic analyzer. This package
holds the analysis so it is unit-tested against synthetic captures long before
a board is on the bench.
"""
