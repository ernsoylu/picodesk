# Example workspace

A reference Simulink workspace built the way production teams actually work —
the first externally authored input to the toolchain, and the fixture behind
the gap analysis in [docs/EXAMPLES_GAP_ANALYSIS.md](../docs/EXAMPLES_GAP_ANALYSIS.md)
and the live regression tests in `tests/matlab_live/`.

What it demonstrates (see the analysis for the full walk-through):

- **Interface-first development**: `models/Interfaces.sldd` declares every VFB
  signal once as `Simulink.Signal` objects; model ports inherit their types
  from it by name. `Externs.sldd` is the (empty) extern-data placeholder.
- **Dictionary references and sharing**: `model1.sldd`/`model2.sldd` each
  reference the shared dictionaries; `model2.sldd` is attached to **both**
  model2 and model3 and carries a calibration parameter (`Thres_T`).
- **Rate-agnostic models**: everything inherited, `FixedStepAuto` — the rate
  group is assigned at integration time on the models page (RTE-002).
- **A deliberate interface contradiction**: model3 compiles `GPO_Led1_B` as
  `single` against the catalogue's declared `boolean`, which the extractor
  diagnoses at the source (G-7).

Build debris (`slprj/`, `*_ert_rtw/`, `*.slxc`) is gitignored; only the
`.slx` models and `.sldd` dictionaries are tracked.

## Reference routing

`routing.json` is a working integration of the sample: rate assignments for
all three models (an integration decision — see the models-page picker),
buttons on pins 14/15 into model1, both decisions across the fast→slow
seqlock boundary into model2, and the LEDs on pins 25/24. It resolves and
generates end to end.

Deliberately not wired, as teaching cases:

- `ADC_Temp1_T` (`single` °C) — the raw `uint16` ADC endpoint doesn't match a
  physical-units port; the conditioning-model idiom in the user guide is the
  answer, and the conditioning model itself is modelling work, not toolchain
  work.
- model3's `GPO_Led1_B` — it compiles as `single` against the catalogue's
  declared `boolean` (the deliberate interface contradiction), so binding it
  to a boolean pin is correctly refused.
- `UART_ExtTemp_T` — no UART input endpoint exists yet (open work).
