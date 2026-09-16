import pandas as pd
import streamlit as st

from core import config as C
from ui import data as D
from ui.charts import rate_chart
from ui.components import dataset_notes, excluded_banner, kpi_tiles, well_header
from ui.sidebar import filters

f = filters()
res = D.get_results()
last_tests = D.get_last_tests().set_index("WELL_NAME")
cal = res.cal.set_index("WELL_NAME")

st.title("Overview", anchor=False)
st.caption("Continuous liquid rate from electrical SCADA data: Q = K x sqrt(3) x V x I / (PDP - PIP). "
           "Diamonds are the well tests used for K, red crosses are suspect tests, grey bands are SCADA gaps, "
           "amber bands are periods on an uncalibrated voltage basis.")
if f["wc_correction"]:
    st.info("Rates, KPIs and cumulative liquid on every page use the water-cut corrected form "
            "**Q = K_dh x X / B_liq** (model M6).", icon=":material/water_drop:")
dataset_notes()

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
    shade = D.shading(well)
    if C.is_excluded(well):
        with st.container(border=True):
            well_header(well, shade.get("run", {}), extra="loaded, not analysed")
            excluded_banner(well, "the virtual-rate analysis")
            st.caption("Its raw signals can still be inspected on the Data quality page.")
        continue
    stats = D.period_stats(well, f["start"], f["end"], f["k_mode"], f["steady_only"], f["wc_correction"])
    with st.container(border=True):
        well_header(well, shade.get("run", {}),
                    extra=f"{stats['rows']:,} rows in period, {stats['rate_rows']:,} with a steady calibrated rate")
        if stats["rows"] == 0:
            st.caption("No SCADA rows in the selected period for this well.")
            continue
        run_now = "previous" if (pd.notna(shade.get("install_date")) and pd.Timestamp(f["end"]) < shade["install_date"]) else "current"
        gas = D.gas_by_well()
        kpi_tiles(stats, cal.loc[well] if well in cal.index else None,
                  last_tests.loc[well] if well in last_tests.index else None, f["k_mode"], run_now,
                  f["wc_correction"], gas.loc[well] if well in gas.index else None)
        series = D.rate_series(well, f["start"], f["end"], f["freq"], f["steady_only"])
        tests = D.tests_in_range(well, f["start"], f["end"])
        st.plotly_chart(rate_chart(well, series, tests, f["freq"], f["steady_only"], f["k_mode"], shade, zoom=zoom),
                        key=f"rate_{well}", config=dict(displaylogo=False, scrollZoom=False))
