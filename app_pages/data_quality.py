import pandas as pd
import streamlit as st

from core import config as C

from core.virtual_rate import gap_intervals, pump_off_intervals
from ui import data as D
from ui.charts import intake_vs_pb_chart, quality_stack, signal_viewer
from ui.components import excluded_notice
from ui.sidebar import filters, wkey
from ui.theme import CATEGORY_LABELS

f = filters()
res = D.get_results(f["scope"])
wells = list(f["wells"])

st.title("Data quality", anchor=False)
st.caption("Every SCADA row is kept and flagged; nothing is dropped silently. "
           "A row needs to be steady (usable and not transient) and on the calibrated voltage basis to carry a rate. "
           "This is the one page that also shows wells excluded from the analysis.")
excluded_notice(f["wells_excluded"], "the analysis; its rows and raw signals are shown here",
                dict(zip(res.excluded["WELL_NAME"], res.excluded["reason"])) if len(res.excluded) else {})

if not wells:
    st.warning("Select at least one well in the sidebar.", icon=":material/filter_alt:")
    st.stop()

# ---------------------------------------------------------------- filter summary (whole history)
st.subheader("Filter summary", anchor=False)
st.caption("Counts over the full SCADA history per well. Flags overlap (a row can be pump_off and bad_dP), "
           "so the flag columns do not sum to the row count.")
fs = res.filter_summary[res.filter_summary["WELL_NAME"].isin(wells)].copy()
fs["first"] = fs["first"].dt.strftime("%Y-%m-%d")
fs["last"] = fs["last"].dt.strftime("%Y-%m-%d")
order = ["WELL_NAME", "excluded", "rows", "usable", "usable_pct", "steady", "steady_pct", "calibrated_basis", "missing_elec", "missing_press",
         "pump_off", "bad_dP", "bad_press_range", "bad_freq", "transient", "temp_unit_c", "first", "last"]
st.dataframe(
    fs[order], hide_index=True,
    column_config={
        "WELL_NAME": st.column_config.TextColumn("Well", pinned=True),
        "excluded": st.column_config.CheckboxColumn("Excluded", help="Loaded and flagged, but never calibrated or analysed."),
        "rows": st.column_config.NumberColumn("Rows", format="localized"),
        "usable": st.column_config.NumberColumn("Usable", format="localized"),
        "usable_pct": st.column_config.ProgressColumn("Usable %", min_value=0, max_value=100, format="%.1f %%"),
        "steady": st.column_config.NumberColumn("Steady", format="localized"),
        "steady_pct": st.column_config.ProgressColumn("Steady %", min_value=0, max_value=100, format="%.1f %%"),
        "calibrated_basis": st.column_config.NumberColumn("Steady + calibrated basis", format="localized",
                                                          help="Steady rows whose VOLTAGE is within the calibrated window; only these carry a rate."),
        "missing_elec": st.column_config.NumberColumn("missing V/I", format="localized"),
        "missing_press": st.column_config.NumberColumn("missing PIP/PDP", format="localized"),
        "pump_off": st.column_config.NumberColumn("pump off", format="localized"),
        "bad_dP": st.column_config.NumberColumn("dP <= 300", format="localized"),
        "bad_press_range": st.column_config.NumberColumn("P out of range", format="localized"),
        "bad_freq": st.column_config.NumberColumn("Hz out of range", format="localized"),
        "transient": st.column_config.NumberColumn("transient", format="localized"),
        "temp_unit_c": st.column_config.NumberColumn("degC rows", format="localized", help="Rows whose MT/INTAKE_TEMP were logged in degC and converted."),
        "first": "First row", "last": "Last row",
    },
)
with st.expander("Flag rules", icon=":material/rule:"):
    st.markdown("""
| Flag | Rule |
|---|---|
| missing_elec | VOLTAGE or AMPERAGE null (X cannot be formed) |
| missing_press | PIP or PDP null |
| pump_off | VOLTAGE < 100 V or AMPERAGE < 5 A or FREQUENCY = 0 |
| bad_dP | PDP - PIP <= 300 psi |
| bad_press_range | PIP outside 50-5000 psi, or WHP > PDP (PDP itself is not bounded) |
| bad_freq | FREQUENCY present, not 0 and outside 30-70 Hz |
| transient | rolling 6-sample CV of AMPERAGE or of dP > 5 % (per well, min 3 samples) |
| temp_unit_c | MT < 150 or INTAKE_TEMP < 100: values are degC, converted to degF for display |
| usable | none of missing_elec, missing_press, pump_off, bad_dP, bad_press_range, bad_freq |
| steady | usable and not transient |
| elec_basis_ok | VOLTAGE within 0.8 x min ... 1.2 x max of the voltages at the well's calibration tests |
""")

# ---------------------------------------------------------------- monthly stacked bar (selected period)
st.subheader("Row categories per month", anchor=False)
st.caption("Each row is counted once, under its first exclusion reason. Blue rows carry a virtual rate.")
mf = res.monthly_flags
m0, m1 = pd.Timestamp(f["start"]).to_period("M").to_timestamp(), pd.Timestamp(f["end"]).to_period("M").to_timestamp()
mf = mf[mf["WELL_NAME"].isin(wells) & (mf["month"] >= m0) & (mf["month"] <= m1)]
st.plotly_chart(quality_stack(mf, wells), key="quality_stack", config=dict(displaylogo=False))

# ---------------------------------------------------------------- raw signal viewer
st.subheader("Raw signal viewer", anchor=False)
with st.container(horizontal=True, vertical_alignment="bottom"):
    well = st.selectbox("Well", wells, key=wkey("dq_well", f["dataset"]), width=220)
    st.caption(f"Resolution: {D.FREQ_LABEL[f['freq']]} (sidebar). Grey bands: pump off > 6 h; light bands: SCADA gaps > 2 days. "
               "All rows are shown here, including excluded ones.")
s = D.signal_series(f["scope"], well, f["start"], f["end"], f["freq"])
if s.empty:
    st.caption("No rows in the selected period.")
else:
    ev = D.window_events(f["scope"], (well,), f["start"], f["end"])
    st.plotly_chart(signal_viewer(well, s, f["freq"], pump_off_intervals(ev, well), gap_intervals(ev, well)),
                    key="signal_viewer", config=dict(displaylogo=False))
    # --- intake pressure against the bubble point ---
    gas = D.gas_by_well(f["scope"])
    if well in gas.index:
        g = gas.loc[well]
        st.markdown("**Intake pressure vs bubble point**")
        st.caption(f"Lab bubble point {g['Pb']:,.0f} psi (Rs {g['Rs_b']:,.0f} scf/stb, oil viscosity "
                   f"{g['oil_visc_cp']:.2f} cp). Over the whole history the intake sits "
                   f"{g['PIP_minus_Pb_median']:+,.0f} psi from Pb and {g['pct_rows_below_Pb']:.0f} % of rows are below "
                   f"it, giving an estimated gas fraction of {g['gvf_median_pct']:.0f} % (median) and "
                   f"{g['gvf_p95_pct']:.0f} % (p95). The gas fraction is {C.GVF_CAVEAT}.")
        st.plotly_chart(intake_vs_pb_chart(well, s, float(g["Pb"]), g.to_dict()),
                        key="intake_pb", config=dict(displaylogo=False))

    cats = D.well_slice(f["scope"], well, f["start"], f["end"])
    if len(cats):
        from core.quality import exclusion_reason
        counts = exclusion_reason(cats).value_counts()
        with st.container(horizontal=True):
            for cat, n in counts.items():
                st.metric(CATEGORY_LABELS.get(cat, cat), f"{n:,}", border=True, help=f"{n / len(cats) * 100:.1f} % of rows in the period")
