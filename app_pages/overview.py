import pandas as pd
import streamlit as st

from core import config as C
from ui import data as D
from ui.charts import rate_chart
from ui.components import dataset_notes_for, excluded_banner, kpi_tiles, well_header
from ui.sidebar import filters

f = filters()
res = D.get_results(f["dataset"])
excluded_reasons = dict(zip(res.excluded["WELL_NAME"], res.excluded["reason"])) if len(res.excluded) else {}
last_tests = D.get_last_tests(f["dataset"]).set_index("WELL_NAME")
cal = res.cal

st.title("Overview", anchor=False)
st.caption("Continuous liquid rate from electrical SCADA data: Q = K x sqrt(3) x V x I / (PDP - PIP). "
           "Diamonds are the well tests used for K, red crosses are suspect tests, grey bands are SCADA gaps, "
           "amber bands are periods on an uncalibrated voltage basis.")
if f["wc_correction"]:
    st.info("Rates, KPIs and cumulative liquid on every page use the water-cut corrected form "
            "**Q = K_dh x X / B_liq** (model M6).", icon=":material/water_drop:")
dataset_notes_for(res)

zoom = st.session_state.get("zoom_window")
if zoom:
    with st.container(horizontal=True, vertical_alignment="center"):
        st.info(f"Zoomed to event window {pd.Timestamp(zoom[0]):%Y-%m-%d} to {pd.Timestamp(zoom[1]):%Y-%m-%d}.", icon=":material/zoom_in:")
        if st.button("Clear zoom", icon=":material/zoom_out:"):
            st.session_state.pop("zoom_window", None)
            st.rerun()

if not f["wells"]:
    st.warning("Select at least one well in the sidebar.", icon=":material/filter_alt:")
    st.stop()

for well in f["wells"]:
    shade = D.shading(f["dataset"], well)
    if well in excluded_reasons:
        with st.container(border=True):
            well_header(well, shade.get("run", {}), extra="loaded, not analysed")
            excluded_banner(well, "the virtual-rate analysis", excluded_reasons[well])
            st.caption("Its raw signals can still be inspected on the Data quality page.")
        continue
    stats = D.period_stats(f["dataset"], well, f["start"], f["end"], f["k_mode"], f["steady_only"], f["wc_correction"])
    with st.container(border=True):
        well_header(well, shade.get("run", {}),
                    extra=f"{stats['rows']:,} rows in period, {stats['rate_rows']:,} with a steady calibrated rate")
        if stats["rows"] == 0:
            st.caption("No SCADA rows in the selected period for this well.")
            continue
        run_now = "previous" if (pd.notna(shade.get("install_date")) and pd.Timestamp(f["end"]) < shade["install_date"]) else "current"
        gas = D.gas_by_well(f["dataset"])
        kpi_tiles(stats, D.cal_row(cal, well),
                  last_tests.loc[well] if well in last_tests.index else None, f["k_mode"], run_now,
                  f["wc_correction"], gas.loc[well] if len(gas) and well in gas.index else None,
                  D.mape_by_well(f["dataset"]), well, res.dataset.has_bubble_point)
        series = D.rate_series(f["dataset"], well, f["start"], f["end"], f["freq"], f["steady_only"])
        tests = D.tests_in_range(f["dataset"], well, f["start"], f["end"])
        st.plotly_chart(rate_chart(well, series, tests, f["freq"], f["steady_only"], f["k_mode"], shade, zoom=zoom),
                        key=f"rate_{well}", config=dict(displaylogo=False, scrollZoom=False))
