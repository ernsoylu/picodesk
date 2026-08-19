# The examples/ workspace vs the toolchain — gap analysis

`examples/models` is a real R2025b workspace built the way production Simulink
teams actually work, and it is the first input to this toolchain that was not
authored by the toolchain's own fixtures. This document records how the sample
is constructed, what happened when the pipeline was run against it, and every
gap that surfaced — each with evidence, a requirement trace, and a proposed
fix. Everything here was verified against the live R2025b installation, not
inferred from reading files.

**Headline: the workspace does not survive first contact.** Batch extraction
fails on the first model before reaching a single port. Behind that blocker
sit two more that would each stop the pipeline on their own, and a tail of
correctness gaps that would bite later and quieter.

## 1. What the sample demonstrates

Three models, four data dictionaries, one user-side ERT build for ground
truth (`model1_ert_rtw/`).

**Dictionary topology** (`DD.DICTIONARYREFERENCE` chains, confirmed via
`Simulink.data.dictionary.open`):

```
model1.slx ── model1.sldd ──┬── Interfaces.sldd   (8 Simulink.Signal entries)
                            └── Externs.sldd      (empty placeholder)
model2.slx ─┐
            ├─ model2.sldd ──┬── Interfaces.sldd
model3.slx ─┘   + Thres_T    └── Externs.sldd
```

Every pattern the user described is present: dictionaries referencing each
other (both model dictionaries pull in `Interfaces.sldd` and `Externs.sldd`),
and one dictionary shared by two models (`model2.sldd` serves model2 **and**
model3).

**The interface catalogue.** `Interfaces.sldd` defines the whole VFB
vocabulary once, centrally, as `Simulink.Signal` objects with a naming
convention that encodes the endpoint kind:

| Signal | Type | Unit | Kind |
|---|---|---|---|
| `GPI_Button1_B`, `GPI_Button2_B` | boolean | — | GPIO in |
| `GPO_Led1_B`, `GPO_Led2_B` | boolean | — | GPIO out |
| `ADC_Temp1_T` | single | °C | ADC in |
| `UART_ExtTemp_T` | single | °C | UART in |
| `MOD_Decision1_B`, `MOD_Decision2_B` | boolean | — | model ↔ model |

**The signal flow** (root ports read from the model archives):

```
buttons ──GPI──▶ model1 ──MOD_Decision1/2──▶ model2 ──GPO──▶ LEDs
temperature ──ADC_Temp1──▶ model2 (compare vs Thres_T)
temperature ──ADC_Temp1──▶ model3 ──GPO_Led1──▶ LED   (alternative consumer)
```

**The development cycle it encodes** — and this is the point of the sample:

1. Interfaces are declared once in a shared dictionary; models *inherit*
   types (`OutDataTypeStr: inherit` on every port) and pick them up by name.
2. Models are **rate-agnostic**: `FixedStepAuto`, every sample time inherited.
   Rate assignment is an integration decision, not a modelling one.
3. Model configuration comes from the Embedded Coder advisor ("Execution
   efficiency + RAM efficiency" objectives, ARM Cortex-M hardware) — not from
   factory defaults.
4. Calibration data lives in the model's dictionary (`Thres_T`,
   `Simulink.Parameter`, single, 30 °C).
5. The same signal (`ADC_Temp1_T`) fans out to two consumers, and two models
   offer alternative implementations of the same output (`GPO_Led1_B`).

The toolchain's own fixtures encode the opposite style on every axis:
explicit types on ports, explicit 1/10/100 ms sample times, factory-default
model configs, no dictionaries. That divergence is exactly where the gaps
are.

## 2. The gap register

Ordered by where the pipeline stops. G-1..G-3 are hard blockers observed in
sequence; the rest were established by targeted probes.

### G-1 · Extraction cannot load a dictionary-attached model — **blocker**

`extract_models` on `examples/models` fails on model1 with *"Unable to find
data dictionary 'model1.sldd'"*, is retried through a full engine restart
(GUI-002 treats it as a session failure), and fails again — the whole batch
dies at model 1 of 3.

Cause: `picodesk_extract.m` calls `load_system` with an absolute path but
never puts the model's directory on the MATLAB path, and `.sldd` attachment
resolves through the path. The toolchain has simply never been fed a
dictionary-attached model before.

Fix: `addpath` the model's folder inside `picodesk_extract` (restore on
cleanup), and close opened dictionaries (`Simulink.data.dictionary.closeAll`)
so back-to-back extractions don't collide on dictionary state.
*(MAT-001; mechanical.)*

### G-2 · Rate-agnostic models kill the batch — **blocker**

All three models compile to `base_rate_s = 0.2` (MATLAB's choice for
`FixedStepAuto` with everything inherited). `rate_group_for()` demands
exactly 0.001/0.01/0.1 s and raises `ValueError`, which aborts the entire
batch extraction.

This is not a broken model — it is the *normal* shape of a model whose rate
is assigned at integration time, which is what the sample's development cycle
does on purpose. Two things are wrong on our side: the policy (the model's
own compiled rate is treated as the only source of a rate group) and the
failure mode (one unclassifiable model aborts the batch instead of being
reported per-model).

Fix, in two steps: (a) per-model diagnosis — an unclassifiable rate marks
that model, never aborts the batch; (b) integration-time rate assignment —
the descriptor records `rate_group: null` for inherited-rate models, the GUI
requires the user to assign a group before routing (models page), and
`picodesk_codegen` forces the assigned rate (`FixedStep`, discrete solver) at
codegen so the generated step matches the dispatcher slot. MAT-002 then runs
against the *assigned* group: model2/model3 carry `single`, so assigning them
`fast_1ms` must hard-error exactly as a modelled 1 ms float does today.
*(RTE-002 / MAT-002 / GUI-001; needs the policy decision, then mechanical.)*

### G-3 · Advisor identifier naming breaks linking and the adapters — **blocker**

The user's models carry the Coder advisor's naming rules
(`CustomSymbolStrGlobalVar = rt$N$M`, `CustomSymbolStrType = $N$M`), and the
shipped `model1_ert_rtw` shows the result: globals `rtU`/`rtY`/`rtM` and
types `ExtU`/`ExtY` — **no model prefix**. Two such models produce duplicate
symbols and cannot link; MAT-003 is violated by any workspace of two or more
default-advisor models. Our adapters reference `<Model>_U`/`<Model>_Y`, which
for these models do not exist. (Our fixtures got prefixed symbols only
because `new_system` factory defaults are `$R$N$M` / `$N$R$M_T` — verified
against this installation.)

Fix: `picodesk_codegen.m` already forces seven config parameters; force the
identifier rules too (`$R$N$M`, `$N$R$M_T`, `$N$M` fields), overriding
whatever the model carries. The step/init functions are already prefixed
(`$R$N$M$F` is untouched by the advisor). *(MAT-003; mechanical, and makes
the pipeline robust to any user config.)*

### G-4 · Hash gating is blind to dictionaries

GUI-001's staleness signal hashes the `.slx` bytes only. Editing `Thres_T`
in `model2.sldd`, or a type in the shared `Interfaces.sldd`, changes model
behaviour and interface — and is a cache hit. The exact failure GUI-001
exists to prevent (stale I/O data presented as current) arrives through the
side door, silently, for both models attached to the shared dictionary.

Fix: hash the model's *dictionary closure* along with the `.slx`. The
closure is exactly what `DataDictionary` + `DataSources` report (recorded at
extraction into the descriptor), so a dictionary edit invalidates every
model attached to it — which is precisely the semantics a shared
`Interfaces.sldd` needs. *(GUI-001 / NFR-2; mechanical once G-1 lands.)*

### G-5 · HAL single-writer keys on the function, not the pin

`resolve_routing` tracks writers by bare consumer ref, so routing
`GPO_Led1_B → hal.hal_gpio_write` (pin A) and `GPO_Led2_B →
hal.hal_gpio_write` (pin B) — which the sample requires — is falsely
rejected as a GUI-009 double-write, because both refs are the string
`hal.hal_gpio_write`. The `hal_arg` that distinguishes the pins is parsed
but not part of the identity.

Fix: key HAL consumer endpoints on `(ref, hal_arg)`. Same pin twice must
still be rejected — model2 and model3 both drive `GPO_Led1_B`, and routing
both to one pin is a genuine double-drive the check exists to catch.
*(GUI-009; mechanical.)*

### G-6 · The HAL vocabulary cannot express the sample's endpoints

Three mismatches between `hal_manifest.json` and the interface catalogue:

- `hal_gpio_read`/`write` are `uint8`; the buttons and LEDs are `boolean`.
  GUI-008's exact-type rule (correctly) refuses the binding, so no GPIO
  endpoint in the sample is routable.
- `hal_adc_read` is `uint16` (raw counts); `ADC_Temp1_T` is `single` °C — a
  physical-units interface. Someone has to own raw→physical conversion, and
  today nothing does: the HAL is raw-only and the models expect physical.
- `UART_ExtTemp_T` has **no endpoint at all** — the manifest has no UART
  channel, and UART0 is currently owned by stdio/telemetry.

Fix: this is a scope decision, not a bug fix. Minimum to route the sample:
boolean GPIO endpoints (or sanctioned boolean↔uint8 coercion at HAL edges),
and a stated position on physical-units HAL (a conditioning-model idiom is
the MAT-002-friendly answer: raw uint16 ADC → slow conversion model →
single). UART input needs a real design (protocol, ownership vs stdio) —
flag, don't improvise. *(GUI-006 / GUI-008.)*

### G-7 · The interface catalogue is not an authority anywhere

`Interfaces.sldd` declares `GPO_Led1_B: boolean`. model3 compiles it as
`single` — the inherited type propagates from `ADC_Temp1_T` straight through
its `Signal Copy` block, and nothing objects: the dictionary's
`Simulink.Signal` entries only constrain ports through signal resolution,
which these models don't enable. Our pipeline reads compiled types, so the
contradiction survives extraction and only surfaces later as a confusing
GUI-008 mismatch between model3 and some consumer, attributed to neither the
model nor the catalogue.

Fix: read the `Simulink.Signal` entries during extraction (the dictionaries
are already open then) and check every port whose name matches a catalogue
entry against the declared type — "model3.GPO_Led1_B is single but
Interfaces.sldd declares boolean" is a diagnosis at the source. The same
catalogue read powers GUI-011: name+type suggestions straight from the
interface dictionary instead of pairwise port comparison. *(GUI-008 /
GUI-011; small once G-1 lands.)*

### G-8 · The sample's one calibration parameter is invisible to XCP

`Thres_T` is the sample's tunable — and it is not tunable.
`DefaultParameterBehavior=Inlined` (verified) plus storage class `Auto`
means ERT bakes `30.0f` into the generated compare; there is no symbol, so
no DWARF, no A2L entry, nothing for GUI-012 to adjust. Beyond this sample:
the pipeline has no policy for model parameters at all — the CAL-page story
(RTE-003) covers the RTE's own structures, and nothing yet surfaces
dictionary `Simulink.Parameter` objects into the CAL window.

Fix (design, not patch): decide the tunable-parameter policy — e.g.
`picodesk_codegen` sets `DefaultParameterBehavior=Tunable` (or requires a
CSC) for parameters the user marks calibratable, places them in the CAL
segment, and the A2L generator picks them up from DWARF as it already does
for RTE structures. Until then, the honest behaviour is a diagnostic:
"model2 defines parameter Thres_T which will be inlined and untunable."
*(GUI-012 / RTE-003 / CAL-002.)*

### G-9 · One bad model burns the engine and the batch

Observed while hitting G-1: an ordinary per-model problem is escalated to a
session failure — the engine is killed and restarted (GUI-002's crash
recovery), the call retried, and the whole batch aborted. Model problems are
data, not crashes: extraction should record a per-model error in the
descriptor scan result and continue, reserving restart for the engine
actually dying. *(GUI-002 / MAT-001; mechanical.)*

## 3. What already holds

Verified against the sample, for balance:

- With the path fixed manually, `picodesk_extract` handles all three models:
  types (`boolean`, `single`) resolve through the dictionaries, widths and
  scaling come out right, and `_normalize_matlab_json` correctly un-collapses
  model3's single-port arrays.
- `C_TYPES` covers every type the catalogue uses; MAT-002 would fire exactly
  as intended if a `single`-carrying model were assigned to the fast group.
- The directory scan (`glob("*.slx")`) is not confused by the `.slxc` cache,
  `slprj/`, or the user's `model1_ert_rtw` build output.
- The `MOD_*` name convention is a perfect match for GUI-011's exact
  name+type auto-resolve: model1's `MOD_Decision1_B` outport meets model2's
  identically named inport.
- Fan-out of one producer to many consumers (`ADC_Temp1_T` → model2 and
  model3) is already legal in routing, as it should be.

## 4. Recommended order

| Step | Gaps | Character |
|---|---|---|
| 1 | G-1, G-3, G-9 | Mechanical; unblocks extraction and linking with no policy questions |
| 2 | G-4, G-5 | Mechanical correctness; small |
| 3 | G-2 | One policy decision (integration-time rate assignment), then mechanical |
| 4 | G-7 | Small, high-leverage diagnostics once dictionaries are readable |
| 5 | G-6, G-8 | Scope decisions: HAL vocabulary (boolean GPIO, physical units, UART) and the tunable-parameter policy |

Steps 1–4 make the sample extract, classify, route and link. Step 5 is what
makes it *mean* something on the bench: LEDs that light and a threshold you
can tune over XCP.
