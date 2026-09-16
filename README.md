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

PHI is the ratio of dP/(sqrt(3) V I) to its value at calibration. It moves when the operating
point changes as well as when the pump degrades. PHI ~ 1 means nothing has changed since
calibration; a sustained drift (7-day median outside 0.95-1.05 for 7 days) means recalibrate or
investigate.

Two K variants are exposed: `K_single` (median K of the well's non-suspect matched tests) and
`K_interp` (linear in time between non-suspect tests, flat outside). Suspect tests are those
whose K has a robust z-score above 3.5 within the well.

## Inputs (read-only, in `data/`)

1. `AI_VW_REAL_TIME_DATA_Sample_Date_13-Sep-2026 V 1.1.xlsx` - 30-min SCADA for the 4 wells.
2. `GC31_DIGIWELLS_81_PARAM_MASTER_DATASET.csv` - well tests (72 for the 4 wells).
3. `ESP_MASTER_DATASET.csv` - pump-run metadata per test (manufacturer, model, stages, depth,
   days from installation). It gives the current run's install date, drawn as a dashed marker on
   the rate charts, and resets the LOW_PIP_TREND baseline per run.

## Validation

MAPE of the three predictions is averaged over the same test set - the 13 matched tests that
have a leave-one-out value (SA-0991H_T's single test is excluded) - and n is reported:

| method | MAPE all | MAPE excl. suspect |
|---|---|---|
| M1 single-K (leave-one-out) | 8.4 | 3.8 |
| M2 walk-forward K | 10.6 | 5.0 |
| Baseline last test carried forward | 13.7 | 5.4 |

## Layout

```
app.py              Streamlit entry point (sidebar + navigation, no computation)
app_pages/          one script per page
ui/                 presentation helpers: cached data access, sidebar, Plotly chart builders
.streamlit/         theme
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
