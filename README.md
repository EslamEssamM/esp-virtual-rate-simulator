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

## Deploy to Streamlit Community Cloud

The repository root is the app root, so Community Cloud needs no extra configuration:
`app.py`, `requirements.txt` and `.streamlit/config.toml` are all at the top level, and the
input files are committed under `data/`.

1. Sign in at [share.streamlit.io](https://share.streamlit.io) with the GitHub account that owns
   this repository. A **private** repository requires granting Streamlit read access to private
   repositories when it asks; the free tier allows one private app.
2. **Create app** -> **Deploy a public app from GitHub** (the same flow serves private repos once
   access is granted), then set:
   - Repository: `<owner>/esp-virtual-rate-simulator`
   - Branch: `main`
   - Main file path: `app.py`
   - Advanced settings -> Python version: **3.11**
3. Deploy. The first load reads the Excel file and writes the parquet cache, which takes about
   5 seconds; later loads come from that cache.

Notes:
- `requirements.txt` is pinned to the versions the test suite runs against, so the deployed app
  reproduces the K values and the MAPE table in this README exactly.
- `cache/` is gitignored and rebuilt on the container at first run. Community Cloud containers are
  ephemeral, so the cache is rebuilt after every restart.
- `requirements-dev.txt` (Playwright, for screenshots) is not installed on the cloud.
- The app holds no secrets and needs no environment variables.

## Datasets

The app serves more than one field. `core/datasets.py` holds a registry; each entry supplies the
loaders and the few facts that differ, and everything downstream is the same code.

| Dataset | Wells | SCADA | Electrical basis |
|---|---|---|---|
| GC31 (Kuwait) | 4 loaded, 3 analysed | 30-min, Apr-2024 to Jul-2026, 69,965 rows | Voltage tag switches between drive side (LV) and motor side (MV) |
| Meleiha (Egypt) | 5 loaded, 5 analysed | 1-10 min, Oct-2018 to Nov-2021, 402,718 rows | Both voltage tags are drive-side; the step-up ratio is absorbed into K |

The sidebar selects the dataset and every cache is keyed on it, so the two never share a number.
Adding a field means adding a loader module and one registry entry.

### Meleiha specifics

- `VOLTAGE := VOLTAGE_VSD_OUT` (drive side) and `AMPERAGE := AMPERAGE_MOTOR` (motor side); the
  originals are kept. The transformer ratio (0.11-0.16) is **not** applied: a constant ratio
  cancels between calibration and prediction, exactly as on GC31's SA-0162. K therefore carries
  the ratio, so its magnitude is not comparable with a motor-side K and no implied efficiency is
  shown. Power factor was never logged; the previous analyst assumed 0.8.
- **Regimes.** A well whose workbook records the transformer ratio or the stage count changing
  runs in more than one electrical regime and is calibrated per regime. The boundary is the
  largest step in the daily median voltage/frequency ratio. M-80 ST splits in May-2019.
- The file's own flags are folded into the app's rules rather than replacing them:
  `pump_off OR NOT PUMP_RUNNING`, and `usable AND NOT GAUGE_FROZEN`.
- Temperatures are already degF, so the degC detection rule is off for this field.
- Well tests carry a date but no time, so the mapper uses the +/-24 h window directly.
- No laboratory bubble point exists, so the free-gas indicator is unavailable. Bo is not measured
  per test, so B_liq uses an assumed constant Bo = 1.05 with a visible caveat.
- The previous analyst's workbook series can be overlaid on the Overview chart. It is never used
  for calibration or MAPE: their factor is recomputed at every row against the allocated rate, so
  it cannot be validated against it.

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
app_pages/          one script per page (overview, data quality, calibration, filter rules,
                    methodology, export)
ui/                 presentation helpers: cached data access, sidebar, Plotly chart builders
.streamlit/         theme
core/
  config.py         well scope (WELLS_ALL / EXCLUDED_WELLS / WELLS), Thresholds, file paths
  load.py           read the two input files, coerce types
  quality.py        row flags under a Thresholds set (nothing is dropped), derived signals
  mapping.py        well test <-> SCADA matching (+/-12 h, widen to +/-24 h)
  calibration.py    suspect screening, K_single, K_interp
  validation.py     M1 leave-one-out, M2 walk-forward, baseline last-test; APE / MAPE
  virtual_rate.py   Q_virtual, PHI, resampling, period statistics
  pvt.py            B_liq water-cut correction and the free-gas-at-intake indicator
  diagnosis.py      rule-based events (no diagnostic matrix)
  pipeline.py       end-to-end run with parquet cache
  export_excel.py   the Excel workbook: native Tables, charts and live K formulas
tests/              pytest suite
data/               read-only inputs
cache/              parquet cache (created on first run)
```

## Filter rules

Every row-level constraint lives in one frozen `core.config.Thresholds` object and is threaded
through the whole pipeline, so the **Filter rules** page can rebuild it from user input and re-run
everything - flags, matching, K, the error against the well tests, the events and the exports - on
the new rules. The page shows what each rule is rejecting right now (over all rows, and over
pump-on rows only, which is how you tell a rule that is doing work from one that is re-flagging
rows another rule already removed), lets every threshold be changed or switched off, and then
compares the result against the defaults: rows carrying a rate, matched and suspect tests, MAPE per
method and K per well. The sidebar warns on every page while non-default rules are in force, and an
export built under them records them on its Read me sheet and carries a `_rules-<id>` suffix.

Re-running under new rules takes a few seconds: the parquet cache now holds the *parsed* SCADA
frame rather than the flagged one, so a rule change costs a re-flag (0.1-0.3 s) rather than a
re-read of the source files.

Defaults are unchanged and are what the numbers in this README and the test suite pin down.

### The dP floor, measured

The 300 psi floor on `dP = PDP - PIP` was a round number rather than a derived one, so it is worth
knowing what it does. Running both fields with the floor at 300 and at 0:

| | dP > 300 (default) | no dP floor |
|---|---|---|
| GC31 rows carrying a rate | 50,041 | 50,041 |
| GC31 K per well | 119.27 / 13.17 / 22.14 | 119.27 / 13.17 / 22.14 |
| GC31 MAPE M1 / M2 | 8.4 % / 10.6 % | 8.4 % / 10.6 % |
| Meleiha rows carrying a rate | 244,450 | 246,930 |
| Meleiha matched tests (suspect) | 48 (5) | 49 (6) |
| Meleiha MAPE M1 / M2 | 14.5 % / 18.4 % | 24.3 % / 30.8 % |

On **GC31 the rule does nothing**: every row it rejects is already rejected for a missing pressure
or a stopped pump. On **Meleiha it is doing real work** - removing it admits 2,480 rows on M-80 ST
at a `dP` of exactly 199.4 psi (a frozen reading, not a measurement), which drags in one more well
test and nearly doubles the error. So the floor stays as the default and is made adjustable rather
than removed; `tests/test_rules.py` pins both halves of this down.

### The PDP ceiling

There is deliberately **no PDP ceiling by default** - discharge pressure spans too wide a range
between fields to bound sensibly - but one is available as a rule. It is the lever for a gauge
pegged at its rail: Meleiha M-80 ST reports `PDP = 6554 psi` (a 16-bit limit) for 56,835 rows, and
because PIP moves underneath it the resulting `dP` of 4,700-5,800 psi looks reasonable, so the dP
floor cannot see it. 49,068 of those rows carry a rate today; setting the ceiling to 6,000 psi
removes them and the error falls from 14.5 % to 12.7 % on M1. SWM A-2-2 on the same field reaches
53,947 psi, which is why one number cannot be imposed on every well.

## Export to Excel

The **Export to Excel** page writes one workbook holding every table in the app, the calibration
factor of each well and one tab per well carrying the same charts. It follows the sidebar, so the
file matches what is on screen: same wells, same period, same K mode, same resolution.

Everything is written as native Excel, not as pictures of the app:

- every table is an Excel **Table**, so it filters, sorts, extends and can be referenced by column
  name; number formats, freeze panes, data bars and colour scales are applied where they help;
- every chart is a real **Excel chart** bound to cell ranges - rate and well tests, PHI, K per
  test, pressures, voltage and current, water cut and B_liq, and the monthly row categories;
- the numbers stay **live**. `K = Q test / X` at each matched test, `K single` is the median of
  the well's non-suspect tests, and each well tab has an editable K cell that drives a
  `Q at K cell` column and its chart series. Tick a test as suspect, or type another K, and the
  calibration, the rates, the charts and the per-test errors all recalculate - the same thing the
  app's what-if slider and suspect screen do. The app's own `Q virtual` column never moves, so the
  workbook always shows both.

A "values only" switch produces the same workbook with every formula replaced by its number.
Sheets: Read me (index and how-to), Wells overview, Calibration K, Matched tests, All well tests,
Validation, Error by method, Data quality, Events, PVT and gas, Wells and pumps, then one tab per
analysed well. Excluded wells appear in the tables with their reason but get no tab: they carry no
K, so there is nothing to chart.

Daily or hourly exports are a few hundred kB and build in a couple of seconds. A 30-minute export
of the full history is roughly 10 MB and takes a minute; the page warns before you ask for one.

## Known dataset facts

- All four wells have a SCADA gap from Jun to Nov 2024.
- The VOLTAGE tag changes basis over time (LV drive side vs MV motor side). K is only valid
  on the basis it was calibrated on, so periods on another basis are shown as "uncalibrated".
- SA-0162_T temperatures are in degC before Oct-2025 and degF after (converted for display).
- The implied overall efficiency (K * 1000 / 78818) is 0.17-0.28 on MV wells, lower than the
  expected 0.5-0.7 for PF * eta_m * eta_p; most likely a tag-basis issue. It is shown as a
  caveat and deliberately not "fixed".
