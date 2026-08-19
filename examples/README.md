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
