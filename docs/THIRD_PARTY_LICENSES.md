# Third-party licence inventory

Every dependency shipped with, linked into, or required by PicoDesk, with
the obligation each one places on a distributed build. Compiled from the SRS
§8 version matrix; the toolchain versions are pinned there and enforced by
`picodesk.buildsys.dependency_checker` (GUI-004).

## Linked into the firmware image

| Component | Version | Licence | Obligation |
|---|---|---|---|
| Raspberry Pi Pico SDK | 1.5.1 (submodule) | BSD-3-Clause | Retain copyright, licence text and disclaimer in redistributions. |
| TinyUSB (via Pico SDK) | bundled with the SDK | MIT | Retain copyright and permission notice. |
| FreeRTOS-Kernel (SMP port) | V11.1.0 (submodule) | MIT | Retain copyright and permission notice. |
| ARM GNU Toolchain runtime (libgcc, newlib) | 12.2.rel1 | GPL-3.0 **with GCC Runtime Library Exception** / BSD-style (newlib) | The Runtime Library Exception permits distributing the compiled firmware under any licence, provided the toolchain is used as an unmodified eligible compilation process. |
| Vector XCPlite | V6.4, vendored unmodified in `target/xcplite/vendor/` (commit in `UPSTREAM_COMMIT`) | **MIT** (© 2026 Vector Informatik) | Retain the copyright and permission notice — `LICENSE` ships alongside the sources. The three vendored files are byte-identical to upstream; everything PicoDesk wrote lives in `target/xcplite/port/`, so there are no modifications to disclose. SRS §8 was amended in v7.1 to MIT (it previously said Apache-2.0). |

## Host toolchain (not redistributed with the firmware)

| Component | Version | Licence | Note |
|---|---|---|---|
| PyQt6 / Qt 6 | 6.5+ | **GPL-3.0** (project decision; the commercial option was declined) | PicoDesk is therefore distributed under GPL-3.0-or-later — see the root `LICENSE`. Distributing a build obliges you to offer recipients the Corresponding Source under the same terms. Lab-internal use triggers no obligation at all, because it is not distribution. |
| pyxcp | 0.21+ | MIT | — |
| pyelftools | 0.31+ | Public domain (Unlicense) | — |
| asammdf | 7.4+ | LGPL-3.0 | Dynamic linking/import keeps the application's own licence intact; modifications to asammdf itself must be published. |
| jsonschema, Jinja2, PyYAML | see `host/pyproject.toml` | MIT / BSD-3-Clause | — |
| MATLAB, Simulink, Embedded Coder | R2025b (SRS §8, v7.1) | Commercial (MathWorks) | Per-seat licence required; nothing MathWorks-owned is redistributed. Generated ERT code is governed by the user's own MATLAB licence. |
| CMake | 3.20+ | BSD-3-Clause | — |

## Development and CI only

| Component | Licence | Note |
|---|---|---|
| Renode | MIT | Emulator used for the system tests; not shipped. |
| matgla/Renode_RP2040 | MIT | RP2040 peripheral models (submodule `external/renode-rp2040`), with a locally patched SIO model — see `sim/README.md`. Not shipped. |
| Robot Framework | Apache-2.0 | Test runner for the Renode suites. |
| pytest, ruff | MIT | — |

## The project's own licence

PicoDesk uses **PyQt6 under GPL-3.0**. That was decided rather than deferred:
the commercial Riverbank/Qt option was considered and declined, so the
combined work is GPL-3.0-or-later and the root `LICENSE` says so.

What that does and does not oblige:

- **Internal use obliges nothing.** The GPL's conditions attach to
  *distribution*. Running PicoDesk on your own machines, however many, is not
  distribution.
- **Distributing a build obliges a source offer.** Recipients get the
  Corresponding Source of the whole application — host toolchain included —
  under GPL-3.0-or-later.
- **The firmware is a separate question, and it is clear.** The `.uf2` links
  no Qt: it is Pico SDK (BSD-3-Clause), FreeRTOS and TinyUSB (MIT), XCPlite
  (MIT), and generated ERT code governed by the user's own MATLAB licence.
  The GCC Runtime Library Exception covers the compiler runtime. Nothing in
  the image is GPL-encumbered by this decision.
- **Generated ERT code stays the user's.** PicoDesk templates the RTE around
  it; it does not become a derivative of PicoDesk.

## Obligations to discharge before distributing a binary

1. **Ship the source offer** required by GPL-3.0 §6, and the root `LICENSE`
   alongside the binary.
2. **Bundle licence texts** for the Pico SDK, FreeRTOS, TinyUSB, XCPlite and
   every MIT/BSD host dependency in the release archive.
3. **State the toolchain provenance** — the GCC Runtime Library Exception
   only applies to an unmodified eligible compilation process, which the
   pinned ARM GCC 12.2.rel1 satisfies.
4. **Note asammdf.** It is LGPL-3.0 and imported, not modified, so the
   application's own licence is unaffected; publish any modification to
   asammdf itself.
