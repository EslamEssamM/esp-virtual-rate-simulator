# ESP Virtual Rate Simulator

Streamlit application that computes a continuous liquid rate for three ESP wells (a fourth is
loaded but excluded, see below) from real-time electrical SCADA data using the Camilleri power-equilibrium method with a single
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

## Well scope

`core/config.py` holds the whole well scope:

```python
WELLS_ALL      = ["SA-0162_T", "SA-0500_T", "SA-0512H_T", "SA-0991H_T"]
EXCLUDED_WELLS = {"SA-0991H_T": "Real-time data cover only Apr-May 2024 (previous pump run) ..."}
WELLS          = [w for w in WELLS_ALL if w not in EXCLUDED_WELLS]
```

An excluded well is still **loaded** from all three files and still appears in the well selector
(marked "excluded", with the reason), in the filter summary and in the raw signal viewer on the
Data quality page. It never enters calibration, K, MAPE, validation, events, the daily series,
period statistics, cumulative liquid or the exports, apart from `excluded_wells.csv`, which lists
it with its reason. Every other page shows a banner instead of numbers for it.

No module outside `config.py` refers to a well by name (a test enforces this), so deleting an
entry from `EXCLUDED_WELLS` is the only change needed to analyse that well, once it has at least
`MIN_MATCHED_TESTS` matched well tests.

## Inputs (read-only, in `data/`)

1. `AI_VW_REAL_TIME_DATA_Sample_Date_13-Sep-2026 V 1.1.xlsx` - 30-min SCADA, 69,965 rows for the
   4 loaded wells (67,240 on the analysed three).
2. `GC31_DIGIWELLS_81_PARAM_MASTER_DATASET.csv` - well tests: 72 loaded, 55 on the analysed wells.
3. `ESP_MASTER_DATASET.csv` - pump-run metadata per test (manufacturer, model, stages, depth,
   days from installation) plus per-test water cut, Bo and B_liq. It gives the current run's
   install date, drawn as a dashed marker on the rate charts, resets the LOW_PIP_TREND baseline
   per run, and supplies the PVT used by the water-cut correction.
4. `pvtdetails_4wells.csv` - one lab PVT model per well: bubble point, solution GOR, Bo, oil
   viscosity, oil SG, API, reservoir temperature. Used for the free-gas indicator. P56 / P58 / P59
   in the 81-parameter CSV are deliberately ignored: they are dataset-wide placeholders.

## PVT: water cut and free gas

**Water-cut correction (model M6, optional, off by default).** The power equation returns the rate
at pump conditions while well tests are at surface, so K silently carries 1/B_liq at the
calibration water cut:

```
B_liq = WC * 1.020 + (1 - WC) * Bo
K_dh  = Q_test * B_liq_test / X_test
Q_M6  = K_dh * X(t) / B_liq(t)
```

WC and Bo are interpolated in time between the well's tests. A sidebar toggle switches every rate,
KPI, period statistic and export between M1 and M6. Physically right, numerically small here:
water cut moves only 55-83 % inside the SCADA period, so the correction stays under 2 % of rate and
M6's leave-one-out error is marginally worse than M1's (4.1 % vs 3.8 % excluding suspect tests).
A test is screened as suspect on K, never on K_dh.

**Free gas at the intake.** Intake pressure against each well's lab bubble point:

| Well | Pb, psi | PIP - Pb, psi | rows below Pb | estimated GVF |
|---|---|---|---|---|
| SA-0162_T | 1535 | -96 | 91 % | ~1 % |
| SA-0500_T | 1490 | +61 | 10 % | ~0 % |
| SA-0512H_T | 1770 | -1337 | 100 % | ~49 % |

SA-0512H_T runs about 1,300 psi below bubble point, so roughly half the volume entering the pump is
free gas. The gas fraction is estimated with an assumed gas gravity of 0.80 and Z = 0.9, neither of
which is in the data: the sign and order of magnitude are solid, the exact percentage is not. It is
reported and raises a `GAS_AT_INTAKE` event, and is never used in the rate.

## Validation

13 of the 55 well tests on the analysed wells match steady SCADA data. All three methods are
scored on the same test set (the tests that have a leave-one-out value), and both the mean
(MAPE) and the median (MdAPE) absolute percentage error are reported, over all matched tests
and excluding the two suspect tests:

| method | MAPE all | median all | MAPE excl. suspect | median excl. suspect |
|---|---|---|---|---|
| M1 single-K (leave-one-out) | 8.4 | 3.4 | 3.8 | 3.3 |
| M6 water-cut corrected (leave-one-out) | 8.9 | 3.7 | 4.1 | 3.6 |
| M2 walk-forward K | 10.6 | 6.1 | 5.0 | 4.7 |
| M6 water-cut corrected (walk-forward) | 10.7 | 5.9 | 5.1 | 5.0 |
| Baseline last test carried forward | 13.7 | 6.0 | 5.4 | 5.7 |

The Calibration & validation page also shows the same figures per well and a sensitivity panel:
adding the excluded well changes nothing under the common-test-set rule (its single matched test
has no leave-one-out value), while dropping that rule would flatter the baseline to 12.8 % and
leave M1 and M2 untouched.

## Layout

```
app.py              Streamlit entry point (sidebar + navigation, no computation)
app_pages/          one script per page
ui/                 presentation helpers: cached data access, sidebar, Plotly chart builders
.streamlit/         theme
core/
  config.py         well scope (WELLS_ALL / EXCLUDED_WELLS / WELLS), thresholds and file paths
  load.py           read the two input files, coerce types
  quality.py        row flags (nothing is dropped), derived signals, FREQ_FILLED
  mapping.py        well test <-> SCADA matching (+/-12 h, widen to +/-24 h)
  calibration.py    suspect screening, K_single, K_interp
  validation.py     M1 leave-one-out, M2 walk-forward, baseline last-test; APE / MAPE
  virtual_rate.py   Q_virtual, PHI, resampling, period statistics
  pvt.py            B_liq water-cut correction and the free-gas-at-intake indicator
  diagnosis.py      rule-based events (no diagnostic matrix)
  pipeline.py       end-to-end run with parquet cache
tests/              pytest suite
data/               read-only inputs
cache/              parquet cache (created on first run)
```

## Known dataset facts

- All four wells have a SCADA gap from Jun to Nov 2024.
- The VOLTAGE tag changes basis over time (LV drive side vs MV motor side). K is only valid
  on the basis it was calibrated on, so periods on another basis are shown as "uncalibrated".
- SA-0162_T temperatures are in degC before Oct-2025 and degF after (converted for display).
- The implied overall efficiency (K * 1000 / 78818) is 0.17-0.28 on MV wells, lower than the
  expected 0.5-0.7 for PF * eta_m * eta_p; most likely a tag-basis issue. It is shown as a
  caveat and deliberately not "fixed".
