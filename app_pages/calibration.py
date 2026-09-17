import pandas as pd
import streamlit as st

from core.config import EFF_DENOM
from core.validation import whatif_k, whatif_mape
from ui import data as D
from ui.charts import k_chart, validation_chart, watercut_chart, whatif_chart
from ui.components import excluded_notice
from ui.sidebar import filters, wkey
from ui.theme import BASE_METHODS, METHOD_LABELS, WC_ALL_METHODS

f = filters()
res = D.get_results(f["dataset"])
wells = list(f["wells_analysed"])
mm = res.matched
cal = res.cal

WC = f["wc_correction"]
DSET = res.dataset
METHODS_SHOWN = WC_ALL_METHODS if WC else BASE_METHODS

st.title("Calibration & validation", anchor=False)
st.caption("K = Q_test / X at each well test that has steady SCADA rows within +/-12 h (widened to +/-24 h when needed). "
           "Suspect tests (robust z > 3.5 within the well) are shown but excluded from K.")

excluded_notice(f["wells_excluded"], "calibration and validation",
                dict(zip(res.excluded["WELL_NAME"], res.excluded["reason"])) if len(res.excluded) else {})
if not wells:
    st.warning("Select at least one analysed well in the sidebar.", icon=":material/filter_alt:")
    st.stop()

# ---------------------------------------------------------------- calibration table
st.subheader("Calibration factor per well", anchor=False)
ct = res.cal[res.cal["WELL_NAME"].isin(wells)].copy()
ct["tests"] = ct["n_tests"].astype(str) + " (+" + ct["n_suspect"].astype(str) + " suspect)"
ct["V window"] = ct["V_lo"].round(0).astype(int).astype(str) + " - " + ct["V_hi"].round(0).astype(int).astype(str) + " V"
ct["first_test"] = ct["first_test"].dt.strftime("%Y-%m-%d")
ct["last_test"] = ct["last_test"].dt.strftime("%Y-%m-%d")
st.dataframe(
    ct[["WELL_NAME"] + (["regime"] if ct["regime"].nunique() > 1 or (ct.groupby("WELL_NAME")["regime"].size() > 1).any() else [])
       + ["K_single", "K_cv_pct"] + (["K_dh_single", "K_dh_cv_pct"] if WC else [])
       + ["tests", "first_test", "last_test", "V_BASIS", "V window"]
       + (["implied_eff"] if DSET.key == "GC31" else [])],
    hide_index=True,
    column_config={
        "WELL_NAME": st.column_config.TextColumn("Well", pinned=True),
        "regime": st.column_config.NumberColumn("Regime", help="Electrical regime: a well whose transformer ratio or "
                                                              "stage count changed mid-life is calibrated per regime."),
        "K_single": st.column_config.NumberColumn("K single", format="%.2f", help="Median K of the non-suspect matched tests."),
        "K_cv_pct": st.column_config.NumberColumn("K spread (CV %)", format="%.1f"),
        "K_dh_single": st.column_config.NumberColumn("K_dh single", format="%.2f",
                                                     help="Downhole-basis K: K x B_liq at the test. Used by the "
                                                          "water-cut corrected rate (M6)."),
        "K_dh_cv_pct": st.column_config.NumberColumn("K_dh spread (CV %)", format="%.1f",
                                                     help="If the water-cut correction removed a real drift, this "
                                                          "would be smaller than the K spread. On this dataset it is not."),
        "tests": "Matched tests", "first_test": "First test", "last_test": "Last test",
        "V_BASIS": st.column_config.TextColumn("V basis", help="LV = drive side (< 800 V), MV = motor side."),
        "V window": st.column_config.TextColumn("Calibrated V window", help="0.8 x min ... 1.2 x max of the voltages at the calibration tests; rows outside carry no rate."),
        "implied_eff": st.column_config.NumberColumn("Implied efficiency", format="%.2f",
                                                     help=f"K x 1000 / {EFF_DENOM:.0f}; the product PF x eta_m x eta_p implied by K."),
    },
)
if DSET.key == "GC31":
    st.caption(":material/warning: Implied overall efficiency is 0.17-0.28 on the MV wells, below the 0.5-0.7 expected for "
               "PF x eta_m x eta_p. This points to a voltage-tag basis issue; it is reported, not corrected.")
else:
    st.caption(f":material/bolt: **{DSET.basis.note}** K therefore also carries the step-up ratio (0.11-0.16 here), so "
               "its magnitude is not comparable with a motor-side K and no implied efficiency is shown. The method "
               "still validates, because a constant ratio cancels between calibration and prediction; where the ratio "
               "changed, the well is split into regimes and calibrated separately.")
if len(res.regimes) and (res.regimes.groupby("WELL_NAME").size() > 1).any():
    multi = res.regimes[res.regimes.groupby("WELL_NAME")["WELL_NAME"].transform("size") > 1]
    st.caption(":material/call_split: Regime boundaries: "
               + "; ".join(f"{w} splits at {pd.Timestamp(g['start'].iloc[-1]):%d %b %Y}"
                           for w, g in multi.groupby("WELL_NAME"))
               + ". The split is the largest step in the daily median voltage/frequency ratio.")

# ---------------------------------------------------------------- matched tests
st.subheader("Matched well tests", anchor=False)
mt = mm[mm["WELL_NAME"].isin(wells)].copy()
st.dataframe(
    mt[["WELL_NAME", "TEST_TS", "Q_LIQ", "RT_X", "K"] + (["WC_FRAC", "B_LIQ", "K_dh"] if WC else [])
       + ["PIP_diff_vs_test", "robust_z", "suspect", "n_steady", "window_h", "V_BASIS",
          "RT_VOLTAGE", "RT_AMPERAGE", "RT_dP", "T_PIP", "RT_PIP", "run"]],
    hide_index=True,
    column_config={
        "WELL_NAME": st.column_config.TextColumn("Well", pinned=True),
        "TEST_TS": st.column_config.DatetimeColumn("Test time", format="YYYY-MM-DD HH:mm"),
        "Q_LIQ": st.column_config.NumberColumn("Q test (BFPD)", format="%.0f"),
        "RT_X": st.column_config.NumberColumn("X at test", format="%.2f", help="sqrt(3) x V x I / dP, median of steady rows in the window"),
        "K": st.column_config.NumberColumn("K", format="%.2f"),
        "WC_FRAC": st.column_config.NumberColumn("Water cut", format="percent",
                                                 help="From ESP_MASTER_DATASET (WATER_CUT_PCT) at this test."),
        "B_LIQ": st.column_config.NumberColumn("B_liq", format="%.4f",
                                               help="WC x 1.020 + (1 - WC) x Bo, rb/stb."),
        "K_dh": st.column_config.NumberColumn("K_dh", format="%.2f", help="K x B_liq: the downhole-basis factor."),
        "PIP_diff_vs_test": st.column_config.NumberColumn("PIP diff (psi)", format="%.0f", help="median SCADA PIP - test PIP (sanity check)"),
        "robust_z": st.column_config.NumberColumn("Robust z", format="%.1f"),
        "suspect": st.column_config.CheckboxColumn("Suspect"),
        "n_steady": st.column_config.NumberColumn("Steady rows"),
        "window_h": st.column_config.NumberColumn("Window (+/- h)"),
        "V_BASIS": "V basis",
        "RT_VOLTAGE": st.column_config.NumberColumn("V", format="%.0f"),
        "RT_AMPERAGE": st.column_config.NumberColumn("I", format="%.1f"),
        "RT_dP": st.column_config.NumberColumn("dP (psi)", format="%.0f"),
        "T_PIP": st.column_config.NumberColumn("PIP test", format="%.0f"),
        "RT_PIP": st.column_config.NumberColumn("PIP SCADA", format="%.0f"),
        "run": "Pump run",
    },
)
with st.expander(f"All {len(res.mapped)} well tests and their match status", icon=":material/list:"):
    allt = res.mapped[res.mapped["WELL_NAME"].isin(wells)][["WELL_NAME", "TEST_TS", "Q_LIQ", "MATCH", "n_window", "n_steady", "nearest_rt_h", "run"]]
    counts = allt["MATCH"].value_counts()
    st.caption(", ".join(f"{k}: {v}" for k, v in counts.items()) + ". Unmatched tests fall before Apr-2024 or inside the Jun-Nov 2024 SCADA gap.")
    st.dataframe(allt, hide_index=True, column_config={
        "TEST_TS": st.column_config.DatetimeColumn("Test time", format="YYYY-MM-DD HH:mm"),
        "Q_LIQ": st.column_config.NumberColumn("Q test (BFPD)", format="%.0f"),
        "nearest_rt_h": st.column_config.NumberColumn("Nearest SCADA row (h)", format="%.1f"),
        "n_window": "Rows in window", "n_steady": "Steady rows", "run": "Pump run"})

# ---------------------------------------------------------------- K vs time
st.subheader("K over time", anchor=False)
cols = st.columns(2)
for i, w in enumerate(wells):
    with cols[i % 2]:
        st.plotly_chart(k_chart(w, mm[mm["WELL_NAME"] == w], D.cal_rows(cal, w), f["t0"], f["t1"]),
                        key=f"k_{w}", config=dict(displaylogo=False))

# ---------------------------------------------------------------- static data disagreements
if "stages_disagree" in res.esp.columns:
    bad = res.esp[res.esp["stages_disagree"] | res.esp["depth_disagree"]]
    if len(bad):
        with st.expander(f":material/warning: Pump stages or depth disagree between sources "
                         f"({len(bad)} of {len(res.esp)} wells)", icon=":material/warning:"):
            st.caption("The operator history log and the analyst workbook record different values. Both are shown; "
                       "neither is picked silently. Resolve with the operator before using pump curves.")
            st.dataframe(res.esp[["WELL_NAME", "PUMP_MODEL", "STAGES_hist", "STAGES_wb", "stages_disagree",
                                  "PUMP_DEPTH_FT_hist", "PUMP_DEPTH_FT_wb", "depth_disagree",
                                  "XFMR_RATIO_wb_raw", "RS_SCF_STB", "OIL_VISC_CP", "RES_TEMP_F"]],
                         hide_index=True, column_config={
                             "WELL_NAME": "Well", "PUMP_MODEL": "Pump",
                             "STAGES_hist": st.column_config.NumberColumn("Stages (log)", format="%.0f"),
                             "STAGES_wb": st.column_config.NumberColumn("Stages (workbook)", format="%.0f"),
                             "stages_disagree": st.column_config.CheckboxColumn("Stages differ"),
                             "PUMP_DEPTH_FT_hist": st.column_config.NumberColumn("Depth ft (log)", format="%.0f"),
                             "PUMP_DEPTH_FT_wb": st.column_config.NumberColumn("Depth ft (workbook)", format="%.0f"),
                             "depth_disagree": st.column_config.CheckboxColumn("Depth differs"),
                             "XFMR_RATIO_wb_raw": "Transformer ratio",
                             "RS_SCF_STB": st.column_config.NumberColumn("Rs scf/stb", format="%.0f"),
                             "OIL_VISC_CP": st.column_config.NumberColumn("Oil visc. cp", format="%.2f"),
                             "RES_TEMP_F": st.column_config.NumberColumn("Res. T degF", format="%.0f")})

# ---------------------------------------------------------------- water cut and B_liq
st.subheader("Water cut and B_liq", anchor=False)
st.caption("The power equation returns the rate at pump conditions; well tests are measured at surface. B_liq is the "
           "ratio between the two. Calibrating K straight to a surface test hides 1/B_liq inside K, which drifts as "
           "water cut rises. The sidebar toggle switches the whole app to the corrected form and adds the K_dh and "
           "M6 columns above.")
wcols = st.columns(2)
for i, w in enumerate(wells):
    with wcols[i % 2]:
        dly = res.daily[res.daily["WELL_NAME"] == w]
        st.plotly_chart(watercut_chart(w, D.pvt_series(f["dataset"], w), dly), key=f"wc_{w}", config=dict(displaylogo=False))

# ---------------------------------------------------------------- validation
st.subheader("Validation against well tests", anchor=False)
st.caption("Predictions per matched test: M1 = single K from the well's other non-suspect tests (leave-one-out); "
           "M2 = K from the most recent earlier non-suspect test (walk-forward); baseline = last well-test rate "
           "carried forward (what engineers use today). Bars are the absolute % error of each prediction."
           + (" M6 is the water-cut corrected twin of M1 and M2: it calibrates K_dh at pump conditions and divides "
              "by B_liq at the test." if WC else ""))
v = res.validation[res.validation["WELL_NAME"].isin(wells)]
c1, c2 = st.columns([3, 2])
with c1:
    st.plotly_chart(validation_chart(v, methods=METHODS_SHOWN), key="validation_chart", config=dict(displaylogo=False))
with c2:
    st.markdown("**Error per method**")
    mo = res.mape_overall[res.mape_overall["method"].isin(METHODS_SHOWN)].copy()
    mo["label"] = mo["method"].map(METHOD_LABELS)
    st.dataframe(mo[["label", "MAPE_all", "MdAPE_all", "n_all", "MAPE_excl_suspect", "MdAPE_excl_suspect", "n_excl_suspect"]],
                 hide_index=True,
                 column_config={"label": "Method",
                                "MAPE_all": st.column_config.NumberColumn("MAPE all (%)", format="%.1f",
                                                                          help="Mean absolute % error over all matched tests."),
                                "MdAPE_all": st.column_config.NumberColumn("median (%)", format="%.1f",
                                                                           help="Median absolute % error: the typical test, unaffected by one bad test."),
                                "n_all": "n",
                                "MAPE_excl_suspect": st.column_config.NumberColumn("MAPE excl. suspect (%)", format="%.1f"),
                                "MdAPE_excl_suspect": st.column_config.NumberColumn("median (%)", format="%.1f"),
                                "n_excl_suspect": "n "})
    st.caption(f"Averaged over the {int(mo['n_all'].max())} matched tests of the "
               f"{len(res.wells_analysed)} analysed wells that have a leave-one-out value, so the three methods are "
               "scored on the same tests. The median column shows the typical test; the mean is pulled up by the two "
               "suspect tests.")
    with st.expander("Per well", icon=":material/table_rows:"):
        mw = res.mape[(res.mape["scope"] != "ALL") & res.mape["scope"].isin(wells)
                      & res.mape["method"].isin(METHODS_SHOWN)].copy()
        mw["label"] = mw["method"].map(METHOD_LABELS)
        st.dataframe(mw[["scope", "label", "MAPE_all", "MdAPE_all", "n_all", "MAPE_excl_suspect", "MdAPE_excl_suspect", "n_excl_suspect"]],
                     hide_index=True,
                     column_config={"scope": "Well", "label": "Method",
                                    "MAPE_all": st.column_config.NumberColumn("MAPE all (%)", format="%.1f"),
                                    "MdAPE_all": st.column_config.NumberColumn("median (%)", format="%.1f"),
                                    "MAPE_excl_suspect": st.column_config.NumberColumn("MAPE excl. susp. (%)", format="%.1f"),
                                    "MdAPE_excl_suspect": st.column_config.NumberColumn("median (%)", format="%.1f"),
                                    "n_all": "n", "n_excl_suspect": "n "})
    with st.expander("Effect of the exclusion and test-set rules", icon=":material/rule_settings:"):
        st.caption("What the headline errors would be under other rules. The first block is what the app reports "
                   "everywhere; the others are shown only so the effect of each rule is visible.")
        sv = res.sensitivity[res.sensitivity["method"].isin(METHODS_SHOWN)].copy()
        sv["label"] = sv["method"].map(METHOD_LABELS)
        st.dataframe(sv[["wells_scope", "test_rule", "label", "MAPE_all", "MdAPE_all", "n_all",
                         "MAPE_excl_suspect", "MdAPE_excl_suspect", "n_excl_suspect"]],
                     hide_index=True,
                     column_config={"wells_scope": "Wells", "test_rule": "Test set", "label": "Method",
                                    "MAPE_all": st.column_config.NumberColumn("MAPE all (%)", format="%.1f"),
                                    "MdAPE_all": st.column_config.NumberColumn("median (%)", format="%.1f"),
                                    "MAPE_excl_suspect": st.column_config.NumberColumn("MAPE excl. susp. (%)", format="%.1f"),
                                    "MdAPE_excl_suspect": st.column_config.NumberColumn("median (%)", format="%.1f"),
                                    "n_all": "n", "n_excl_suspect": "n "})
        st.markdown(f"""
- Adding the excluded well changes **nothing** under the common-test-set rule: its single matched test has no
  leave-one-out value, so it contributes no validated test. That is why it is excluded.
- Dropping the common-test-set rule lets that one test into the baseline only, flattering it from
  {mo.loc[mo['method'] == 'BASE_LAST_TEST', 'MAPE_all'].iloc[0]:.1f} % to
  {sv.loc[(sv['method'] == 'BASE_LAST_TEST') & (sv['test_rule'] != 'Common test set'), 'MAPE_all'].iloc[0]:.1f} %
  while M1 and M2 are unchanged, which would make the method look worse against the baseline than it is.
""")
        if len(res.excluded):
            st.dataframe(res.excluded[["WELL_NAME", "excluded_by", "scada_rows", "well_tests", "pump", "reason"]],
                         hide_index=True,
                         column_config={"WELL_NAME": "Well", "excluded_by": "Excluded by",
                                        "scada_rows": st.column_config.NumberColumn("SCADA rows", format="localized"),
                                        "well_tests": "Well tests", "pump": "Pump", "reason": st.column_config.TextColumn("Reason", width="large")})

# ---------------------------------------------------------------- what-if
st.subheader("What-if: override K", anchor=False)
st.caption("Slide K away from the calibrated value to see how the predicted rate at each matched test and the continuous rate respond. "
           "Nothing is saved; the sidebar K mode is unaffected.")
wcols = st.columns([1, 2])
with wcols[0]:
    w = st.selectbox("Well", wells, key=wkey("whatif_well", f["dataset"]))
    _cr = D.cal_row(cal, w)
    k_ref = float(_cr["K_single"]) if _cr is not None else 1.0
    k_new = st.slider("K", min_value=round(k_ref * 0.5, 2), max_value=round(k_ref * 1.5, 2), value=round(k_ref, 2),
                      step=round(max(k_ref / 200, 0.01), 2), key=f"whatif_k_{w}", help=f"Calibrated K single = {k_ref:.2f}")
    t = whatif_k(mm, w, k_new)
    m_all, m_ok = whatif_mape(t)
    t_ref = whatif_k(mm, w, k_ref)
    r_all, r_ok = whatif_mape(t_ref)
    with st.container(horizontal=True):
        st.metric("K", f"{k_new:.2f}", f"{(k_new / k_ref - 1) * 100:+.1f} % vs calibrated", delta_color="off", border=True)
        st.metric("MAPE excl. suspect", f"{m_ok:.1f} %", f"{m_ok - r_ok:+.1f} pp", delta_color="inverse", border=True,
                  help="Mean absolute % error at this well's non-suspect matched tests with the what-if K, versus K single.")
    st.dataframe(t, hide_index=True, column_config={
        "TEST_TS": st.column_config.DatetimeColumn("Test", format="YYYY-MM-DD"),
        "Q_LIQ": st.column_config.NumberColumn("Q test", format="%.0f"),
        "RT_X": st.column_config.NumberColumn("X", format="%.2f"),
        "K": st.column_config.NumberColumn("K at test", format="%.2f"),
        "suspect": st.column_config.CheckboxColumn("Suspect"),
        "Q_whatif": st.column_config.NumberColumn("Q what-if", format="%.0f"),
        "APE_whatif": st.column_config.NumberColumn("APE %", format="%.1f")})
with wcols[1]:
    series = D.rate_series(f["dataset"], w, f["start"], f["end"], "D" if f["freq"] == "30min" else f["freq"], True)
    tests_w = D.tests_in_range(f["dataset"], w, f["start"], f["end"])
    st.plotly_chart(whatif_chart(w, series, k_ref, k_new, f["freq"], tests=tests_w, whatif_at_tests=t),
                    key="whatif_chart", config=dict(displaylogo=False))
    st.caption("Filled diamonds are the measured tests; open diamonds are what the what-if K predicts at the same SCADA state. "
               "Slide K until the open diamonds sit on the filled ones.")
