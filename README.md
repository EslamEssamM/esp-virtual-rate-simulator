# ESP Virtual Rate Simulator

Streamlit application that computes a continuous liquid rate for four ESP wells from
real-time electrical SCADA data using the Camilleri power-equilibrium method with a single
calibration factor **K** per well, validates it against well tests, and lets you explore
diagnosis and trends for any period.

The dataset is a fixed demonstration: the two input files in `data/` are read-only and are
never modified. A parquet cache of the flagged SCADA frame is written to `cache/` on first run.

## Run

```bash
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run app.py
```

Tests (they use the real dataset and assert the expected K values, matched tests and MAPE):

```bash
.venv\Scripts\python.exe -m pytest -q
```

Command-line summary of the whole pipeline (no Streamlit):

```bash
.venv\Scripts\python.exe -m core.pipeline
```

## Physics (the only model)

Camilleri power-equilibrium equation (SPE-127593 Eq. 1): pump absorbed power equals motor
generated power

```
dP * Q / (58847 * eta_p) = sqrt(3) * V * I * PF * eta_m / 746
```

Power factor, motor efficiency, pump efficiency, cable/transformer ratios and the
downhole-to-surface volume factor are unknown, so they are lumped into one factor per well:

```
X         = sqrt(3) * VOLTAGE * AMPERAGE / (PDP - PIP)
Q_virtual = K * X
K         = Q_test / X_at_test          (calibrated per well from matched well tests)
```

**FREQUENCY is not an input to this equation** and is never used in the rate. It is only
displayed (forward-filled up to 24 h, then monthly median).

Health indicator (Camilleri PHI concept):

```
PHI = (dP / (sqrt(3) * V * I)) / (same ratio at calibration)
```

1.0 = as calibrated; a sustained drift of more than 5 % means recalibration is recommended.

Two K variants are exposed: `K_single` (median K of the well's non-suspect matched tests) and
`K_interp` (linear in time between non-suspect tests, flat outside). Suspect tests are those
whose K has a robust z-score above 3.5 within the well.

## Layout

```
app.py              Streamlit UI (six pages, no computation)
core/
  config.py         thresholds and file paths
  load.py           read the two input files, coerce types
  quality.py        row flags (nothing is dropped), derived signals, FREQ_FILLED
  mapping.py        well test <-> SCADA matching (+/-12 h, widen to +/-24 h)
  calibration.py    suspect screening, K_single, K_interp
  validation.py     M1 leave-one-out, M2 walk-forward, baseline last-test; APE / MAPE
  virtual_rate.py   Q_virtual, PHI, resampling, period statistics
  diagnosis.py      rule-based events (no diagnostic matrix)
  pipeline.py       end-to-end run with parquet cache
tests/              pytest suite
data/               read-only inputs
cache/              parquet cache (created on first run)
```

## Known dataset facts

- All four wells have a SCADA gap from Jun to Nov 2024.
- SA-0991H_T has real-time data only for Apr-May 2024 with one matching test.
- The VOLTAGE tag changes basis over time (LV drive side vs MV motor side). K is only valid
  on the basis it was calibrated on, so periods on another basis are shown as "uncalibrated".
- SA-0162_T temperatures are in degC before Oct-2025 and degF after (converted for display).
- The implied overall efficiency (K * 1000 / 78818) is 0.17-0.28 on MV wells, lower than the
  expected 0.5-0.7 for PF * eta_m * eta_p; most likely a tag-basis issue. It is shown as a
  caveat and deliberately not "fixed".
