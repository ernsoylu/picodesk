# Software Requirements Specification (SRS) v7.1

**Python-MATLAB Virtual Functional Bus (VFB) & Calibration Toolchain for RP2040**

> **Amendments since v7.0**
> - **§8 MATLAB / Python versions (v7.1).** The pinned MATLAB release is now
>   **R2025b** — a project decision: R2025b is the release actually in use, so
>   pinning to R2023b–R2024b would have validated against a toolchain nobody
>   runs. Python widens to 3.9–3.12 to match that release's engine bindings.
> - **§8 XCPlite licence (v7.1).** Corrected from Apache-2.0 to **MIT**;
>   upstream `vectorgrp/XCPlite` relicensed (© 2026 Vector Informatik).
>
> This is the authoritative requirements baseline for PicoDesk. Every feature, design decision,
> and code review must trace back to a requirement ID in this document. Changes to this document
> require a version bump and changelog entry.

## 1. Introduction & Scope

### 1.1 Purpose & Glossary

This toolchain acts as a lightweight **Virtual Functional Bus (VFB)** (defined here strictly as a design-time signal routing bus with Sender/Receiver semantics) and orchestrator for the Raspberry Pi Pico. It allows users to batch-import 20–30 independent Simulink models, route their signals to each other and a default C-driver HAL, generate a deterministic multi-rate Run-Time Environment (RTE), and perform live XCP calibration.

### 1.2 Target Architecture Constraints

* **MCU:** Raspberry Pi Pico (RP2040, ARM Cortex-M0+, 264kB SRAM, **No FPU**).
* **Core 0 (Fast Path):** Hardware Timer ISR executing entirely from SRAM. *(Note: Core 0 still runs the FreeRTOS idle task and participates in SMP, but the fast-path control loop preempts it via hardware IRQ).*
* **Core 1 (Slow Path):** FreeRTOS Kernel (SMP port) handling slower rate groups, XCP, and watchdog.

### 1.3 Out of Scope (v1.0)

* **On-Target Hardware Debugging:** OpenOCD/GDB SMP halting is unstable; debugging relies exclusively on XCP/MDF4 and telemetry.
* **NvM Persistence:** Parameters revert to compiled defaults on power cycle.
* **Apple Silicon (ARM64):** Toolchain fragmentation restricts v1.0 to Windows/Linux x64.
* **Software-in-the-Loop (SIL):** Host-side RTE emulation is excluded.
* **XCP Security:** XCP Seed/Key protection is out of scope (Lab-use only).

---

## 2. GUI & Orchestration (REQ-GUI)

| ID | Requirement | Acceptance Criteria |
| --- | --- | --- |
| **GUI-001** | **Single Source of Truth** | GUI hashes imported `.slx` files. If a hash changes, GUI forces a re-export of the model descriptor and warns of stale I/O data. |
| **GUI-002** | **Persistent MATLAB Session** | Backend maintains a persistent `matlab.engine` session with crash-recovery to eliminate cold-starts. |
| **GUI-003** | **Workspace Versioning (Reinstated)** | The tool saves routing configurations to a versioned JSON schema. Loading old schemas triggers an automatic, safe migration prompt. |
| **GUI-004** | **Dependency Checker (Reinstated)** | Upon startup, the GUI verifies the existence and versions of `arm-none-eabi-gcc`, CMake, and Pico SDK in the `PATH`, disabling builds if missing. |
| **GUI-005** | **Diagnostic Console (Reinstated)** | An embedded terminal streams stdout/stderr from MATLAB and CMake. Errors/Warnings are regex-parsed, color-coded, and hyperlinked to the source line. |
| **GUI-006** | **HAL Manifest & Default SDK** | The toolchain ships with a minimal default HAL (GPIO, ADC, PWM). The GUI reads `hal_manifest.json` and rejects mapping fast-loop models to non-ISR-safe HAL functions. |
| **GUI-007** | **Hierarchical Routing Matrix** | The VFB UI uses a 3-pane split (Producers, Connections, Consumers) avoiding node-graph spaghetti. |
| **GUI-008** | **Strict Type Filtering** | Selecting a Producer dynamically hides/disables Consumer ports where data type, dimension, or fixed-point scaling do not perfectly match. |
| **GUI-009** | **Single-Writer & Cascade Delete** | Bound Inports display a locked state (`🔒`). Deleting a model automatically unlinks and unlocks all its dependent Consumers. |
| **GUI-010** | **Rate Transition Indicators** | Connections between mismatched base rates display a "Rate Transition: ZOH/Seqlock" badge. |
| **GUI-011** | **Auto-Resolve Wizard** | "Suggest Bindings" highlights exact Name + Type matches, offering a Preview + Undo step for bulk wiring. |
| **GUI-012** | **Integrated XCP Dashboard** | A "Live Tuning" panel uses `pyxcp` over CDC-Serial to adjust parameters and record ASAM MDF4 files. |

---

## 3. MATLAB Codegen & Profiling (REQ-MAT)

| ID | Requirement | Acceptance Criteria |
| --- | --- | --- |
| **MAT-001** | **Batch Model Extraction** | Python parses a directory of `.slx` files, outputting a monolithic JSON descriptor containing I/O, data types, and base rates. |
| **MAT-002** | **Float-Point Hard Error** | Backend parses compiled model metrics. Internal `single`/`double` types in models mapped to the fast-loop trigger a **hard build error** (no software-float in 1ms loop). |
| **MAT-003** | **A2L Generation & Symbol Hygiene** | ERT generates A2L segments matching the custom RP2040 linker (`RAM_SHARED`). Symbols are strictly prefixed per-model to prevent collisions across 30 models. |

---

## 4. RTE & Concurrency Layer (REQ-RTE)

| ID | Requirement | Acceptance Criteria |
| --- | --- | --- |
| **RTE-001** | **Implicit Communication** | All routed signals are copied into private task-level shadow buffers during a Copy-In phase prior to `model_step()` execution. |
| **RTE-002** | **Multi-Rate Dispatch Policy** | The fastest base rate triggers via a Core 0 Hardware Timer Alarm. Slower rates execute as FreeRTOS Core 1 tasks, triggered via `vTaskNotifyGiveFromISR()`. |
| **RTE-003** | **XCP CAL_PAGE Transactional Consistency** | Calibration uses standard XCP Calibration Pages (`SET_CAL_PAGE`). Core 1 writes to an offline RAM page; Core 0 swaps the active page pointer *only* at the `model_step()` boundary upon a page-switch command, ensuring multi-parameter transactional consistency. |
| **RTE-004** | **Bi-directional Bounded Seqlocks** | Cross-core signals >32 bits use Seqlocks for *both* directions. Writers disable local IRQs. Readers use a bounded retry loop (max 3), dropping to the last-known-good stale data on failure. All routed signals are initialized to zero/producer-defaults at boot. |
| **RTE-005** | **Coherent DAQ Data Path** | The fast-path ISR writes coherent DAQ frame snapshots into an SRAM2 ring buffer. The Core 1 XCPlite task drains this ring buffer over USB, preventing cross-bank contention and torn frames. |

---

## 5. Build, Memory & Safety (REQ-BLD)

| ID | Requirement | Acceptance Criteria |
| --- | --- | --- |
| **BLD-001** | **XIP-Flash Jitter Elimination** | Generated code applies SDK `__not_in_flash_func()` to the fast ISR, all fast `model_step()` functions, and RTE Copy routines to force SRAM execution. |
| **BLD-002** | **Deliberate Bank Separation** | Custom `.ld` script forces Core 0 Stack/BSS to `SRAM0`, Core 1 Stack/BSS to `SRAM1`, and RTE Shared Data (Seqlocks, DAQ Ring, CAL Pages, FreeRTOS Heap) to `SRAM2`. |
| **BLD-003** | **Execution Health Telemetry** | RTE uses the RP2040 64-bit µs timer to measure fast-loop execution time. GUI displays ISR Utilization (%), Overrun Counts, and Seqlock Faults. |
| **BLD-004** | **SRAM/Flash Sizing Report (Reinstated)** | Python orchestrator statically estimates RAM/Flash footprint prior to CMake. GUI displays a sizing report and halts if estimates exceed 200 KB SRAM or 1.5 MB Flash. |
| **BLD-005** | **Task Priorities & Stack Budgets** | The RTE generator explicitly assigns FreeRTOS task priorities (XCP = Low, Rate Groups = Monotonic) and allocates defined stack sizes (e.g., 2KB per task). Stack high-water marks are telemetered to the GUI. |
| **BLD-006** | **HardFault Capture** | A custom HardFault handler is injected to capture the faulting PC/LR registers and write them to a reserved uninitialized RAM section, surviving a watchdog reset for printout on the next boot. |
| **BLD-007** | **Cross-Core Watchdog Monitor** | Core 0 ISR increments a heartbeat. Core 1 watchdog task verifies advancement before feeding the RP2040 hardware watchdog. |
| **BLD-008** | **Reproducible Build Flags** | CMake enforces `-ffile-prefix-map` and bans `__DATE__`. Successive builds of an unchanged model yield matching SHA-256 `.uf2` hashes. |

---

## 6. Calibration & Measurement (REQ-CAL)

| ID | Requirement | Acceptance Criteria |
| --- | --- | --- |
| **CAL-001** | **XCP-on-CDC Integration** | Vector XCPlite is integrated with a custom transport shim for USB CDC-ACM. Throughput supports a DAQ list of ≥50 signals at 100 Hz. |
| **CAL-002** | **DWARF-Aware Address Patching** | The `.elf` is compiled with `-g`. Python uses `pyelftools` to parse DWARF `RECORD_LAYOUT`, resolving inner member VMA addresses of nested Simulink structs. |

---

## 7. Non-Functional Requirements (NFR)

| ID | Requirement | Acceptance Criteria |
| --- | --- | --- |
| **NFR-1** | **Fast-Loop Jitter** | The Core 0 hardware timer ISR exhibits a maximum dispatch jitter of **≤ 50 µs at p99.99**, measured via logic analyzer under full XCP DAQ load over 1,000,000 cycles. |
| **NFR-2** | **Incremental Toolchain Speed** | Using the persistent MATLAB session, a routing-only change (hash-gated to prevent ERT re-generation of unchanged models) completes RTE templating and CMake relinking in **≤ 45 seconds**. |
| **NFR-3** | **Global Critical Section Budget** | No FreeRTOS or HAL critical section executing on **ANY core** shall hold the kernel SMP spinlock or mask interrupts for longer than **15 µs**, protecting the Core 0 timer ISR deadline. |

---

## 8. Dependencies & Version Matrix

| Dependency | Required Version | Architectural Note & Licensing |
| --- | --- | --- |
| **MATLAB / ERT** | R2025b | Pinned to the release in use. Defines supported Python Engine bindings. Commercial License required. |
| **Python** | 3.9 – 3.12 | Must align exactly with MATLAB Engine release (R2025b supports 3.9–3.12). |
| **PyQt** | PyQt6 / Qt 6.5+ | Async UI threading. (GPL/Commercial). |
| **RP2040 SDK** | v1.5.1+ | Hardware timer and spinlock APIs. (BSD-3-Clause). |
| **FreeRTOS Kernel** | v11.1.0+ (SMP) | Stable `configNUMBER_OF_CORES` behavior. (MIT). |
| **ARM GCC** | 12.2.rel1 | Standard cross-compiler; dictates DWARF parsing format. |
| **CMake** | 3.20+ | Required by Pico SDK. |
| **Vector XCPlite** | v5.x+ | Core 1 XCP Slave. *(Note: Vector provides this as Open Source / **MIT** — corrected in v7.1, upstream relicensed — but license terms must be retained in source).* |
| **pyxcp** | 0.21+ | XCP Master implemented in Python. (MIT). |
| **pyelftools** | 0.31+ | DWARF/ELF parsing for A2L post-processing. (Public Domain). |
| **asammdf** | 7.4+ | ASAM MDF4 file generation for DAQ logging. (LGPL). |
