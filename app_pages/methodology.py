import numpy as np
import pandas as pd
import streamlit as st

from core import config as C
from ui import data as D
from ui.theme import METHOD_LABELS

res = D.get_results()
cal = res.cal.set_index("WELL_NAME")

st.title("Methodology", anchor=False)
st.caption("What the app actually computes, step by step, with the parameters and the numbers obtained on this dataset. "
           "Everything on this page is produced by the same code as the other pages (core/), nothing is typed in by hand.")

# ------------------------------------------------------------------ 1. physics
st.header("1. Physics: Camilleri power equilibrium", anchor=False)
c1, c2 = st.columns([3, 2])
with c1:
    st.markdown("The pump's absorbed hydraulic power equals the power the motor generates (SPE-127593, Eq. 1):")
    st.latex(r"\frac{\Delta P \cdot Q}{58847\,\eta_p} \;=\; \frac{\sqrt{3}\, V\, I\, PF\, \eta_m}{746}")
    st.markdown("with dP in psi, Q in BFPD, V in volts, I in amps. Power factor, motor efficiency, pump efficiency, "
                "cable and transformer ratios and the downhole-to-surface volume factor are all unknown for these wells, "
                "so they are lumped into **one calibration factor K per well**:")
    st.latex(r"X \;=\; \frac{\sqrt{3}\, V\, I}{PDP - PIP}")
    st.latex(r"Q_{virtual} \;=\; K \cdot X \qquad\qquad K \;=\; \frac{Q_{test}}{X_{at\ test}}")
    st.markdown("Every rate on every page is exactly `K x X` on that row. **FREQUENCY is not part of the equation** and is never "
                "used in the rate; it is only displayed (forward-filled up to 24 h, then monthly median).")
with c2:
    with st.container(border=True):
        st.markdown("**Health indicator PHI**")
        st.latex(r"PHI \;=\; \frac{\Delta P / (\sqrt{3}\, V\, I)}{\left[\Delta P / (\sqrt{3}\, V\, I)\right]_{calibration}}")
        st.caption(C.PHI_HELP)
        st.markdown("The calibration value is the median of dP/(sqrt(3) V I) over the well's non-suspect matched tests.")
    with st.container(border=True):
        st.markdown("**Implied overall efficiency**")
        st.latex(r"PF\,\eta_m\,\eta_p \;\approx\; \frac{K \cdot 1000}{78818}")
        st.caption("Reported as a caveat only. On the MV wells it is 0.17-0.28, below the 0.5-0.7 expected; "
                   "most likely the voltage tag is not on the motor basis. It is not corrected.")

# ------------------------------------------------------------------ 2. well scope
st.header("2. Well scope: analysed and excluded wells", anchor=False)
st.markdown(f"""
{len(res.wells_all)} wells are **loaded** from every input file. {len(res.wells_analysed)} are **analysed**:
{", ".join(res.wells_analysed)}.

An excluded well keeps all of its rows, flags and raw signals, and stays visible on the Data quality page, but it
never enters calibration, K, MAPE, validation, events, the daily series, period statistics or cumulative liquid.
The exclusion list lives in one place (`core/config.py: EXCLUDED_WELLS`); no other module refers to a well by name,
so a well becomes analysed again by deleting its entry once it has at least {C.MIN_MATCHED_TESTS} matched tests.
""")
if len(res.excluded):
    st.dataframe(res.excluded[["WELL_NAME", "excluded_by", "scada_rows", "scada_first", "scada_last", "well_tests", "pump", "reason"]],
                 hide_index=True,
                 column_config={"WELL_NAME": "Well", "excluded_by": "Excluded by",
                                "scada_rows": st.column_config.NumberColumn("SCADA rows", format="localized"),
                                "scada_first": st.column_config.DatetimeColumn("First row", format="YYYY-MM-DD"),
                                "scada_last": st.column_config.DatetimeColumn("Last row", format="YYYY-MM-DD"),
                                "well_tests": "Well tests", "pump": "Pump",
                                "reason": st.column_config.TextColumn("Reason (verbatim from config)", width="large")})

# ------------------------------------------------------------------ 3. inputs
st.header("3. Inputs", anchor=False)
m = res.meta
st.markdown(f"""
| File | Used for | This dataset |
|---|---|---|
| `{C.RT_FILE.name}` (sheet `{C.RT_SHEET}`) | 30-min SCADA: WHP, PIP, PDP, MT, INTAKE_TEMP, FREQUENCY, VOLTAGE, AMPERAGE | {m['n_rows']:,} rows loaded, {m['n_rows_analysed']:,} analysed; {pd.Timestamp(m['rt_start']):%d %b %Y} to {pd.Timestamp(m['rt_end']):%d %b %Y} |
| `{C.WT_FILE.name}` | Well tests: liquid rate, PIP/PDP/WHP at test, frequency | {m['n_tests']} tests loaded, {m['n_tests_analysed']} on analysed wells |
| `{C.ESP_MASTER_FILE.name}` | Pump run metadata: manufacturer, model, stages, depth, days from installation | run boundaries drawn on the charts |

Files are read once, filtered to the 4 wells, timestamps parsed, non-numeric placeholders (`DATA_UNRECORDED`, `MISSING_HARDWARE_SPEC`) coerced to NaN.
The flagged SCADA frame is cached as parquet in `cache/`; the source files are never modified.
""")

# ------------------------------------------------------------------ 3. flags
st.header("4. Row quality flags (nothing is dropped)", anchor=False)
st.markdown(f"""
Every SCADA row is kept and receives boolean flags. Only rows that pass all of them, are steady, and are on the calibrated voltage basis carry a rate.

| Flag | Rule |
|---|---|
| `missing_elec` | VOLTAGE or AMPERAGE null |
| `missing_press` | PIP or PDP null |
| `pump_off` | VOLTAGE < {C.PUMP_OFF_V:.0f} V or AMPERAGE < {C.PUMP_OFF_I:.0f} A or FREQUENCY = 0 |
| `bad_dP` | PDP - PIP <= {C.MIN_DP:.0f} psi (pump not developing head / gauge fault) |
| `bad_press_range` | PIP outside {C.PIP_RANGE[0]:.0f}-{C.PIP_RANGE[1]:.0f} psi, PDP outside {C.PDP_RANGE[0]:.0f}-{C.PDP_RANGE[1]:.0f} psi, or WHP > PDP |
| `bad_freq` | FREQUENCY present, not 0, outside {C.FREQ_RANGE[0]:.0f}-{C.FREQ_RANGE[1]:.0f} Hz |
| `transient` | rolling {C.TRANSIENT_WINDOW}-sample coefficient of variation of AMPERAGE or of dP > {C.TRANSIENT_CV * 100:.0f} % (per well, min {C.TRANSIENT_MIN_PERIODS} samples) |
| `temp_unit_c` | MT < {C.TEMP_C_MT_MAX:.0f} or INTAKE_TEMP < {C.TEMP_C_IT_MAX:.0f}: logged in degC, converted to degF for display |
| `usable` | none of missing_elec, missing_press, pump_off, bad_dP, bad_press_range, bad_freq |
| `steady` | usable and not transient |
| `V_BASIS` | LV if VOLTAGE < {C.LV_MV_SPLIT_V:.0f} V (drive side) else MV (motor side) |
| `elec_basis_ok` | VOLTAGE within {C.ELEC_BASIS_LO} x min ... {C.ELEC_BASIS_HI} x max of the voltages at the well's calibration tests |
""")
fs = res.filter_summary[["WELL_NAME", "rows", "usable", "usable_pct", "steady", "steady_pct", "calibrated_basis"]]
st.dataframe(fs, hide_index=True, column_config={
    "WELL_NAME": "Well", "rows": st.column_config.NumberColumn("Rows", format="localized"),
    "usable": st.column_config.NumberColumn("Usable", format="localized"), "usable_pct": st.column_config.NumberColumn("Usable %", format="%.1f"),
    "steady": st.column_config.NumberColumn("Steady", format="localized"), "steady_pct": st.column_config.NumberColumn("Steady %", format="%.1f"),
    "calibrated_basis": st.column_config.NumberColumn("Steady + calibrated basis (carry a rate)", format="localized")})

# ------------------------------------------------------------------ 4. mapping
st.header("5. Well test to SCADA mapping", anchor=False)
counts = res.mapped["MATCH"].value_counts()
st.markdown(f"""
For each well test, take the **steady** SCADA rows within +/-{C.MAP_WINDOW_H} h of the test timestamp. If fewer than {C.MAP_MIN_ROWS} rows,
widen to +/-{C.MAP_WINDOW_WIDE_H} h. If still fewer, the test is `NO_RT_DATA` (no rows at all) or `INSUFFICIENT_STEADY_DATA`.
For a matched test, the SCADA state is the **median** of VOLTAGE, AMPERAGE, PIP, PDP, WHP, dP, X and MT over those rows, and
`PIP_diff_vs_test = median SCADA PIP - test PIP` is kept as a sanity check.

Result on this dataset: **{counts.get('MATCHED', 0)} matched** of {len(res.mapped)} tests on the analysed wells
({counts.get('NO_RT_DATA', 0)} with no SCADA rows in the window, {counts.get('INSUFFICIENT_STEADY_DATA', 0)} with too few steady rows).
The unmatched tests fall before Apr-2024 or inside the Jun-Nov 2024 SCADA gap.
""")

# ------------------------------------------------------------------ 5. calibration
st.header("6. Calibration of K", anchor=False)
st.markdown(f"""
1. `K = Q_test / X` for every matched test.
2. **Suspect screening** inside each well with a robust z-score: `|K - median(K)| / ({C.MAD_SCALE} x MAD) > {C.ROBUST_Z_MAX}` marks the test suspect.
   Suspect tests stay visible everywhere (red crosses) but are excluded from K.
3. **K single** = median K of the non-suspect tests.
4. **K interpolated** = K linear in time between non-suspect tests, flat before the first and after the last (default in the sidebar).
5. A well needs at least {C.MIN_MATCHED_TESTS} non-suspect matched tests to be calibrated: with a single test K cannot be validated (no leave-one-out, no walk-forward). Such a well is reported as excluded, never silently calibrated.
""")
ct = res.cal.copy()
ct["first_test"] = ct["first_test"].dt.strftime("%Y-%m-%d"); ct["last_test"] = ct["last_test"].dt.strftime("%Y-%m-%d")
st.dataframe(ct[["WELL_NAME", "K_single", "K_min", "K_max", "n_tests", "n_suspect", "first_test", "last_test", "V_BASIS", "PHI_base", "implied_eff"]],
             hide_index=True, column_config={
                 "WELL_NAME": "Well", "K_single": st.column_config.NumberColumn("K single", format="%.2f"),
                 "K_min": st.column_config.NumberColumn("K min", format="%.2f"), "K_max": st.column_config.NumberColumn("K max", format="%.2f"),
                 "n_tests": "Tests used", "n_suspect": "Suspect", "first_test": "First", "last_test": "Last", "V_BASIS": "V basis",
                 "PHI_base": st.column_config.NumberColumn("dP/(sqrt3 V I) at calibration", format="%.2f"),
                 "implied_eff": st.column_config.NumberColumn("Implied efficiency", format="%.2f")})
sus = res.matched[res.matched["suspect"]]
st.caption("Suspect tests: " + "; ".join(f"{r['WELL_NAME']} {r['TEST_TS']:%Y-%m-%d} ({r['Q_LIQ']:.0f} BFPD, K={r['K']:.1f}, robust z={r['robust_z']:.1f})" for _, r in sus.iterrows()))

# ------------------------------------------------------------------ 6. worked example
st.header("7. Worked example on one matched test", anchor=False)
mm = res.matched
labels = {f"{r['WELL_NAME']}  {r['TEST_TS']:%Y-%m-%d %H:%M}  ({r['Q_LIQ']:.0f} BFPD)": i for i, r in mm.iterrows()}
pick = st.selectbox("Matched test", list(labels), key="method_example")
r = mm.loc[labels[pick]]
x = np.sqrt(3) * r["RT_VOLTAGE"] * r["RT_AMPERAGE"] / r["RT_dP"]
e1, e2 = st.columns([1, 1])
with e1:
    st.markdown(f"""
| Step | Value |
|---|---|
| Steady rows in +/-{int(r['window_h'])} h window | {int(r['n_steady'])} |
| median VOLTAGE | {r['RT_VOLTAGE']:.1f} V ({r['V_BASIS']}) |
| median AMPERAGE | {r['RT_AMPERAGE']:.2f} A |
| median PDP - PIP | {r['RT_PDP']:.0f} - {r['RT_PIP']:.0f} = {r['RT_dP']:.0f} psi |
| X = sqrt(3) x V x I / dP | sqrt(3) x {r['RT_VOLTAGE']:.1f} x {r['RT_AMPERAGE']:.2f} / {r['RT_dP']:.0f} = **{x:.3f}** |
| Well test rate | {r['Q_LIQ']:.0f} BFPD |
| K = Q_test / X | {r['Q_LIQ']:.0f} / {x:.3f} = **{r['K']:.2f}** |
| Robust z within well | {r['robust_z']:.2f} -> {'suspect, excluded' if r['suspect'] else 'accepted'} |
| PIP sanity check | SCADA {r['RT_PIP']:.0f} vs test {r['T_PIP']:.0f} psi (diff {r['PIP_diff_vs_test']:+.0f}) |
""")
with e2:
    w = r["WELL_NAME"]
    if w in cal.index:
        ks = cal.loc[w, "K_single"]
        st.markdown(f"""
With the well's **K single = {ks:.2f}** the same SCADA state gives
`Q = {ks:.2f} x {x:.3f} = {ks * x:.0f} BFPD`, an error of **{abs(ks * x - r['Q_LIQ']) / r['Q_LIQ'] * 100:.1f} %** against the test
(this is the in-sample error; the validation page uses leave-one-out and walk-forward K so the test being predicted is never part of its own K).

PHI at this test: `({r['RT_dP']:.0f} / {r['RT_P_elec_kVA']:.1f}) / {cal.loc[w, 'PHI_base']:.2f} = {(r['RT_dP'] / r['RT_P_elec_kVA']) / cal.loc[w, 'PHI_base']:.3f}`
""")

# ------------------------------------------------------------------ 7. validation
st.header("8. Validation against well tests", anchor=False)
st.markdown("""
Three predictions per matched test, each compared with the measured rate as an absolute percentage error (APE):

| Method | K used | Meaning |
|---|---|---|
| M1 leave-one-out | median K of the well's *other* non-suspect tests | how good a single K is when this test is unseen |
| M2 walk-forward | K of the most recent *earlier* non-suspect test | what a field engineer would have used at the time |
| Baseline | none: the last well-test rate carried forward | current practice |

Both errors are reported for every method: **MAPE** (mean absolute % error, which one bad test can dominate) and
**MdAPE** (median absolute % error, the typical test). Each is shown over all matched tests and excluding the suspect
tests, and all three methods are scored on the same test set: the tests that have a leave-one-out value. n is shown
next to every figure.
""")
mo = res.mape_overall.copy(); mo["label"] = mo["method"].map(METHOD_LABELS)
st.dataframe(mo[["label", "MAPE_all", "MdAPE_all", "n_all", "MAPE_excl_suspect", "MdAPE_excl_suspect", "n_excl_suspect"]], hide_index=True,
             column_config={"label": "Method",
                            "MAPE_all": st.column_config.NumberColumn("MAPE all (%)", format="%.1f"),
                            "MdAPE_all": st.column_config.NumberColumn("median all (%)", format="%.1f"), "n_all": "n",
                            "MAPE_excl_suspect": st.column_config.NumberColumn("MAPE excl. suspect (%)", format="%.1f"),
                            "MdAPE_excl_suspect": st.column_config.NumberColumn("median excl. suspect (%)", format="%.1f"),
                            "n_excl_suspect": "n "})
st.caption("The Calibration & validation page has the same table per well, plus the effect of the exclusion and "
           "test-set rules.")

# ------------------------------------------------------------------ 8. continuous rate
st.header("9. Continuous virtual rate and period statistics", anchor=False)
st.markdown(f"""
- A row carries a rate when it is `usable`, on the calibrated voltage basis (`elec_basis_ok`) and X is finite. Charts and statistics use the **steady** subset by default; the sidebar toggle only adds transient rows to the drawing.
- Hourly / daily series are **medians** of the rows in each bin; empty bins stay empty so gaps are visible.
- **Metered liquid (bbl)** = sum over hours of (hourly median rate x 1 h), counting only hours that have a valid steady rate; the coverage % says how many hours of the period were metered. It is not calendar production.
- **Uptime %** = rows not `pump_off` / all rows. **Data quality %** = usable rows / all rows.
- **Pump runs**: the install date of the current run comes from the ESP master dataset (test date minus days from installation). Rows before it belong to the previous run and are labelled so; K is calibrated on the current run's tests.
""")

# ------------------------------------------------------------------ 9. diagnosis
st.header("10. Rule-based event detection (no diagnostic matrix)", anchor=False)
st.markdown(f"""
Run on the per-well daily series (medians over rows that are not pump-off; rates over steady rows). Every event carries a one-line explanation and its evidence numbers.

| Event | Rule | Severity |
|---|---|---|
| SCADA_GAP | no rows for > {C.GAP_DAYS} days | info |
| PUMP_OFF | consecutive pump_off rows spanning > {C.PUMP_OFF_HOURS} h | warning |
| VOLTAGE_BASIS_CHANGE | daily median VOLTAGE changes > {C.VOLT_CHANGE_PCT} % between consecutive days with data (K not valid across it) | warning |
| VOLTAGE_STEP | same, {C.VOLT_STEP_PCT}-{C.VOLT_CHANGE_PCT} % | info |
| TEMP_UNIT_SWITCH | daily majority of the temp_unit_c flag flips (3-day smoothed) | info |
| RATE_STEP | daily rate differs > {C.RATE_STEP_PCT} % from the trailing {C.RATE_STEP_TRAIL_DAYS}-day median and holds >= {C.RATE_STEP_MIN_DAYS} days | warning |
| PHI_DRIFT | {C.PHI_ROLL_DAYS}-day median PHI outside {C.PHI_BAND[0]}-{C.PHI_BAND[1]} for >= {C.PHI_DRIFT_MIN_DAYS} days: recalibration recommended | critical |
| BACKPRESSURE | daily WHP > {C.BACKPRESSURE_FACTOR} x its trailing {C.BACKPRESSURE_TRAIL_DAYS}-day median for >= {C.BACKPRESSURE_MIN_DAYS} days | warning |
| LOW_PIP_TREND | {C.LOW_PIP_ROLL_DAYS}-day median PIP > {C.LOW_PIP_PCT} % below the baseline = SCADA PIP at the first matched test of the same pump run | warning |
| SUSPECT_TEST | from the calibration screening | critical |
""")
ev = res.events.groupby("type").size().rename("count").reset_index()
st.dataframe(ev, hide_index=True, column_config={"type": "Event type", "count": "Events on this dataset"})

# ------------------------------------------------------------------ 10. what is deliberately not done
st.header("11. Scope and limitations", anchor=False)
st.markdown("""
- Only the constant-K power method is implemented: **no diagnostic matrix, no pump-curve model**.
- A well whose SCADA cannot validate a K is excluded rather than given an unvalidated rate; the reason is shown verbatim wherever it would have appeared.
- K absorbs everything unknown (PF, efficiencies, transformer ratio, volume factor). It is therefore only valid for the electrical basis it was calibrated on and for the pump run it was calibrated on; both are flagged on the charts rather than corrected.
- PHI moves with the operating point as well as with pump condition; it is a drift indicator, not a pump-health measurement.
- Well tests with fewer than 6 steady SCADA rows within 24 h are not used, and no row is ever dropped from the dataset: every exclusion is a visible flag on the Data quality page.
""")
