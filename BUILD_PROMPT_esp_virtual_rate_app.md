# Build prompt: ESP Virtual Rate Simulator (Streamlit)

Copy everything below the line into your AI coding tool. The reference implementation (`vr_pipeline.py`) is in the same folder and already produces the expected numbers, so the tool can read it.

---

## Task

Build a Python + Streamlit application called **ESP Virtual Rate Simulator** that calculates a continuous liquid rate for 4 ESP wells from real-time electrical SCADA data using the Camilleri power-equilibrium method with one overall calibration factor K per well, validates it against well tests, and lets a user explore diagnosis and trends for any period. The dataset is fixed (a demonstration), so load it once, cache it, and never modify the source files.

Do **not** implement the diagnostic matrix or pump-curve models. Only the constant-K power method.

## Input files (read-only, in `data/`)

1. `AI_VW_REAL_TIME_DATA_Sample_Date_13-Sep-2026 V 1.1.xlsx`, sheet `AI_VW_REAL_TIME_DATA`, 69,965 rows, 30-min cadence (some 1-min bursts). Columns:
   `WELL_NAME, GC_NAME, PRODUCTION_METHOD, TIME_STAMP (datetime), WHP, WHT (all null), FLP, PIP, PDP, INTAKE_TEMP, MT, FREQUENCY, VOLTAGE, AMPERAGE`
   Pressures psi, temperatures °F (see units caveat), FREQUENCY Hz, VOLTAGE V, AMPERAGE A.
   Wells: `SA-0162_T, SA-0500_T, SA-0512H_T, SA-0991H_T`. Date range Apr-2024 to Jul-2026.
2. `GC31_DIGIWELLS_81_PARAM_MASTER_DATASET.csv`, 1,942 well tests for 163 wells. Use only the 4 wells above (69 tests). Columns needed:
   `WELL_NAME, Test Timestamp, P26: Liquid Rate / BFPD, P27: Oil Rtae /BOPD, P29: W.C %, P30: GOR / SCF/STB, P34: Mtr. Freq. /hz, P35: WHP Psi, P39: P.Intake Pressure /psi, P40: P. Discharge Pressure /psi, P11: Pump type, P13: nr. Of Stages, P64: pump intake /TVD, P55: Fluid desity ppg, P81: Well test validiation`
   Non-numeric placeholders such as `DATA_UNRECORDED`, `MISSING_HARDWARE_SPEC` must be coerced to NaN.

## Physics (the only model)

Camilleri power-equilibrium equation (SPE-127593 Eq. 1): pump absorbed power = motor generated power

```
ΔP · Q / (58847 · ηp) = √3 · V · I · PF · ηm / 746
```

Power factor, motor efficiency, pump efficiency, cable/transformer ratios and the downhole-to-surface volume factor are unknown, so they are lumped into one factor per well:

```
X = √3 · VOLTAGE · AMPERAGE / (PDP − PIP)
Q_virtual = K · X
K = Q_test / X_at_test        (calibrated per well, see below)
```

Frequency is **not** an input to this equation. Health indicator (Camilleri PHI concept):

```
PHI = (ΔP / (√3·V·I)) / (same ratio at calibration)     → 1.0 = as calibrated; sustained drift > 5% = recalibrate
```

## Pipeline (implement as `core/` modules, pure pandas, no Streamlit imports)

### 1. `load.py`
Load both files, filter to the 4 wells, parse timestamps, coerce numerics, cache with `st.cache_data` at the app layer.

### 2. `quality.py` — row flags (all boolean columns, keep every row, never drop)
| Flag | Rule |
|---|---|
| `missing_press` | PIP or PDP null |
| `pump_off` | VOLTAGE < 100 or AMPERAGE < 5 or FREQUENCY == 0 |
| `bad_dP` | (PDP − PIP) ≤ 300 psi |
| `bad_press_range` | PIP ∉ [50, 5000] or PDP ∉ [300, 6000] or WHP > PDP |
| `bad_freq` | FREQUENCY not null, ≠ 0 and ∉ [30, 70] |
| `transient` | rolling 6-sample CV of AMPERAGE > 5% or of ΔP > 5% (per well, min 3 samples) |
| `temp_unit_c` | MT < 150 or INTAKE_TEMP < 100 → values are °C; convert both to °F (SA-0162 logged °C until Oct-2025) |
| `usable` | none of missing_press, pump_off, bad_dP, bad_press_range, bad_freq |
| `steady` | usable and not transient |
| `V_BASIS` | 'LV' if VOLTAGE < 800 else 'MV' (voltage tag switches between drive side and motor side) |
| `elec_basis_ok` | VOLTAGE within 0.8× min … 1.2× max of the voltages seen in that well's calibration tests |

Also derive `dP = PDP − PIP`, `X`, `P_elec_kVA = √3·V·I/1000`, `FREQ_FILLED` (forward-fill ≤ 24 h, then monthly median; for display only).

### 3. `mapping.py` — well test ↔ SCADA
For each test: take steady rows within ±12 h of `Test Timestamp`; if fewer than 6 rows, widen to ±24 h; if still fewer, mark `NO_RT_DATA` / `INSUFFICIENT_STEADY_DATA`. For matched tests store the median of VOLTAGE, AMPERAGE, PIP, PDP, WHP, dP, X, MT, plus `PIP_diff_vs_test = median SCADA PIP − test PIP` as a sanity check, and `K = Q_test / X`.
Expected: 14 matched of 69 (the rest fall before Apr-2024 or inside the Jun–Nov 2024 SCADA gap).

### 4. `calibration.py`
- `K_single` per well = median K of matched tests, after removing outliers with robust z (|K − median| / (1.4826·MAD) > 3.5 → `suspect = True`).
- `K_interp`: K linearly interpolated in time between non-suspect tests, flat before the first and after the last.
- Expose both; default display uses `K_interp`, with a toggle.
- Expected K_single: SA-0162 ≈ 119.3, SA-0500 ≈ 13.2, SA-0512H ≈ 22.1, SA-0991H ≈ 17.3 (one test only, cannot be validated). Expected suspect tests: SA-0162 2026-01-15 (539 BFPD) and SA-0500 2026-04-26 (1761 BFPD).

### 5. `validation.py` — three predictions per matched test
- `M1_LOO`: K_single from the other tests of the well × X at this test.
- `M2_WALK`: K from the most recent earlier non-suspect test.
- `BASE_LAST_TEST`: last well-test rate carried forward (what engineers use today).
Report APE per test and MAPE per well and overall, with and without suspect tests.
Expected MAPE (all / excl. suspect): M1 8.4 / 3.8, M2 10.6 / 5.0, baseline 13.7 / 5.4.

### 6. `virtual_rate.py`
Compute `Q_virtual` on steady rows with `elec_basis_ok`; also `PHI`. Resample to hourly and daily medians for charts. Aggregate stats for any date range: mean/median rate, cumulative liquid (bbl), uptime % (rows not pump_off), data quality % (usable/rows), PHI drift.

### 7. `diagnosis.py` — rule-based event detection (no matrix)
Run on the daily series per well and return an event table `(well, start, end, type, severity, evidence)`:
- `SCADA_GAP`: no rows for > 2 days.
- `PUMP_OFF`: pump_off for > 6 h.
- `VOLTAGE_BASIS_CHANGE`: daily median VOLTAGE changes by > 30% between consecutive days with data (e.g. SA-0162 2024 V → 364 V in Dec-2024; SA-0512H 2084 → 1818 V in Aug-2025).
- `TEMP_UNIT_SWITCH`: temp_unit_c flag flips.
- `RATE_STEP`: daily Q_virtual median changes > 15% vs trailing 7-day median and holds for ≥ 3 days.
- `PHI_DRIFT`: 7-day median PHI outside 0.95–1.05 for ≥ 7 days → "recalibration recommended".
- `BACKPRESSURE`: daily WHP > 1.5× its 30-day median for ≥ 2 days (SA-0162 Nov-2025).
- `LOW_PIP_TREND`: 30-day PIP median down > 15% vs the calibration baseline (SA-0500 during 2025, SA-0512H 2025).
- `SUSPECT_TEST`: from calibration.
Each event carries a one-line plain-English explanation and the evidence numbers.

## Streamlit UI (`app.py`, multipage via sidebar)

Global sidebar: well selector (multi), date range picker (default full range), K mode toggle (single / interpolated), resolution (30-min / hourly / daily), "show only steady rows" toggle.

**Page 1 – Overview**: KPI tiles per selected well (current virtual rate, K, last test, last test error %, PHI, data quality %, uptime %). A rate chart per well: Q_virtual line, well tests as points, matched tests circled, suspect tests as red ×, shaded SCADA gaps.

**Page 2 – Data quality**: stacked bar of row flags per well per month; table of the filter summary (rows, usable %, steady %); raw signal viewer (V, I, Hz, PIP, PDP, WHP, MT) for the selected period with pump-off periods shaded.

**Page 3 – Calibration & validation**: matched-test table (well, timestamp, Q_test, X, K, PIP_diff, suspect); K vs time chart per well; validation bar chart (APE for M1, M2, baseline per test); MAPE summary table; a "what-if" slider to manually override K and see the effect.

**Page 4 – Period analysis**: choose a period (or two periods A/B to compare). Show: mean/median rate, cumulative liquid, uptime, quality, PHI range, rate histogram, signal medians, and the events that occurred. Period B vs A delta table.

**Page 5 – Diagnosis**: event timeline (Gantt-style bands per well) + event table with filters; clicking an event zooms Page-1 chart to that window with the evidence signals plotted.

**Page 6 – Export**: download buttons for the daily virtual rate CSV, events CSV, calibration table, and a PDF/HTML summary of the selected period.

## Engineering requirements

- Python 3.11, pandas, numpy, plotly (interactive charts), streamlit, openpyxl. No database; parquet cache of the processed frame in `cache/` created on first run.
- All computation in `core/` with unit tests (`pytest`) that assert the expected K values and MAPEs above within ±2%.
- Charts in plotly with shared x-axis zoom across signals; hover shows all values at that timestamp.
- Handle missing FREQUENCY gracefully everywhere (it is null in ~50% of rows); never use it in the rate.
- Never drop rows silently; every exclusion must be visible on the Data quality page.
- README with run instructions (`streamlit run app.py`) and the physics summary above.

## Known dataset facts to display as notes in the app

- All 4 wells have a SCADA gap from Jun to Nov 2024.
- SA-0991H_T has real-time data only for Apr–May 2024 with one matching test.
- The VOLTAGE tag changes basis over time (LV drive side vs MV motor side); K is only valid on the basis it was calibrated on, so periods on another basis are shown as "uncalibrated".
- SA-0162_T temperatures are in °C before Oct-2025 and °F after.
- Implied overall efficiency (K·1000/78818) is 0.17–0.28 on MV wells, lower than the expected 0.5–0.7 for PF·ηm·ηp; likely a tag-basis issue. Show this as a caveat, do not "fix" it.

## Acceptance checklist

1. App loads in < 10 s from cache and reproduces the 14 matched tests, the K values and the MAPE table.
2. Selecting SA-0162_T and Nov-2025 to Feb-2026 shows the rate drop, a BACKPRESSURE event, a PHI_DRIFT event, and the 15-Jan-2026 test marked suspect.
3. Period comparison for SA-0500_T, 2025-H1 vs 2026-H1, shows the declining rate and the LOW_PIP_TREND event.
4. Unit tests pass.
