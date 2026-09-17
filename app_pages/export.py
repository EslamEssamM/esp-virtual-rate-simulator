import pandas as pd
import streamlit as st

from ui import data as D
from ui import export as XP
from ui.components import excluded_notice
from ui.sidebar import filters

f = filters()
res = D.get_results(f["scope"])
wells = tuple(f["wells"])
analysed = tuple(f["wells_analysed"])

st.title("Export to Excel", anchor=False)
st.caption("One workbook with every table on the other pages, the calibration factor of each well "
           "and a tab per well carrying the same charts. Everything is written as native Excel: "
           "Tables with filters, real charts bound to cell ranges and live formulas, not pictures.")

if not wells:
    st.warning("Select at least one well in the sidebar.", icon=":material/filter_alt:")
    st.stop()

# ---------------------------------------------------------------- what is being exported
with st.container(border=True):
    st.markdown("**Scope**  - taken from the sidebar, so the workbook matches what you are looking at.")
    c = st.columns(4, gap="small")
    with c[0]:
        st.metric("Wells", f"{len(analysed)} analysed", f"{len(wells) - len(analysed)} excluded"
                  if len(wells) > len(analysed) else None, delta_color="off", delta_arrow="off", border=True)
    with c[1]:
        st.metric("Period", f"{pd.Timestamp(f['start']):%d %b %y} - {pd.Timestamp(f['end']):%d %b %y}",
                  D.FREQ_LABEL[f["freq"]], delta_color="off", delta_arrow="off", border=True)
    with c[2]:
        st.metric("K mode", "Interpolated" if f["k_mode"] == "interp" else "Single",
                  "B_liq corrected (M6)" if f["wc_correction"] else "Q = K x X (M1)",
                  delta_color="off", delta_arrow="off", border=True)
    with c[3]:
        rows = XP.series_rows(f["scope"], analysed, f["start"], f["end"], f["freq"], f["steady_only"])
        st.metric("Series rows", f"{rows:,}",
                  "steady rows only" if f["steady_only"] else "steady and transient",
                  delta_color="off", delta_arrow="off", border=True)
    st.caption("Change any of these in the sidebar. Excluded wells appear in the cross-well tables "
               "and in 'Wells and pumps' with their reason, but get no tab of their own: they carry "
               "no K, so there is nothing to chart.")

excluded_notice(f["wells_excluded"], "the per-well tabs (they appear in the tables, with the reason)",
                dict(zip(res.excluded["WELL_NAME"], res.excluded["reason"])) if len(res.excluded) else {})

if rows > 150_000:
    st.warning(f"{rows:,} series rows at {D.FREQ_LABEL[f['freq']]}. The file will be large and slow "
               "to build. Switch the sidebar resolution to hourly or daily for a workbook that "
               "opens quickly.", icon=":material/hourglass_top:")

# ---------------------------------------------------------------- options
st.subheader("What goes in", anchor=False)
c1, c2 = st.columns(2)
with c1:
    per_well_tabs = st.toggle("One tab per well, with its charts", value=True, key="xl_tabs",
                              help="Tiles, an editable K, the rate, PHI, K, pressure, electrical, "
                                   "water-cut and data-quality charts, and the series behind them.")
    include_quality = st.toggle("Data quality sheet", value=True, key="xl_quality",
                                help="Flag counts per well and the monthly row categories.")
    include_events = st.toggle("Diagnosis events sheet", value=True, key="xl_events",
                               help="Every detected event overlapping the period, with its evidence.")
    include_pvt = st.toggle("PVT and gas sheet", value=bool(len(res.lab_pvt) or len(res.test_pvt)),
                            key="xl_pvt", disabled=not (len(res.lab_pvt) or len(res.test_pvt)),
                            help="Laboratory PVT, intake against the bubble point and the water cut "
                                 "at each test. Disabled when the field has no PVT.")
with c2:
    live_formulas = st.toggle("Live Excel formulas", value=True, key="xl_live",
                              help="K is recomputed in Excel as the median of the well's non-suspect "
                                   "matched tests, and each well tab gets an editable K cell that "
                                   "drives a 'Q at K' column and its chart series. Turn this off to "
                                   "get a workbook of plain numbers.")
    if live_formulas:
        st.caption(":material/functions: With formulas on, the workbook recalculates: change a K, "
                   "or tick a test as suspect, and the rates, charts and errors follow. The app's own "
                   "numbers stay in their own columns and never move.")
    else:
        st.caption(":material/pin: Values only. Every cell is a number, nothing recalculates.")

# ---------------------------------------------------------------- build and download
st.subheader("Build", anchor=False)
if st.button("Build the workbook", icon=":material/table_view:", type="primary", width="content"):
    st.session_state["xl_ready"] = True

if st.session_state.get("xl_ready"):
    with st.spinner("Writing sheets, tables and charts..."):
        data = XP.workbook(f["scope"], analysed, f["start"], f["end"], f["k_mode"], f["freq"],
                           f["steady_only"], f["wc_correction"], live_formulas, per_well_tabs,
                           include_events, include_quality, include_pvt)
    with st.container(horizontal=True, vertical_alignment="center"):
        st.download_button("Download .xlsx", data=data,
                           file_name=XP.filename(f["scope"], f["start"], f["end"]),
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/download:", type="primary")
        st.caption(f"{len(data) / 1e6:.1f} MB. Rebuilt whenever the sidebar or the options above change.")

# ---------------------------------------------------------------- what the file contains
st.subheader("What the workbook contains", anchor=False)
st.markdown("""
| Sheet | What is on it |
|---|---|
| Read me | The selection, the three equations, how to change K, and a hyperlinked index. |
| Wells overview | Every Overview tile, one row per well, with rate and error comparison charts. |
| Calibration K | K per well and regime. With formulas on, the median of the matched tests. |
| Matched tests | Every matched test, with `K = Q test / X` as a formula and the suspect flag. |
| All well tests | Every test loaded, matched or not, with the match status and why. |
| Validation | The per-test prediction of each method and its error. |
| Error by method | MAPE and median error, overall, per well and under each exclusion rule. |
| Data quality | Flag counts per well, and rows per month by category. |
| Events | Diagnosis events overlapping the period, with the evidence behind each one. |
| PVT and gas | Laboratory PVT, intake against the bubble point, water cut per test. |
| Wells and pumps | Pump runs, electrical regimes and excluded wells with their reason. |
| One tab per well | Tiles, an editable K, the charts from Overview, Calibration and Data quality, and the series behind them. |
""")
with st.expander("How the workbook stays adaptable", icon=":material/function:"):
    st.markdown("""
- Every table is a real **Excel Table**, so it filters, sorts and extends, and other formulas can
  refer to its columns by name.
- Every chart is a real **Excel chart** bound to cell ranges, not an image. Change the numbers and
  the chart moves; change the chart and the data stays.
- The yellow cell near the top of each well tab is that well's **calibration factor**. It starts as
  a formula pointing at the Calibration K sheet; type a number over it and the `Q at K cell` column,
  its dashed chart line and the per-test error all follow. Delete your value to go back.
- On **Matched tests**, `K` is `Q test / X` and `K used for calibration` is blank for a suspect test.
  Tick a test as suspect, or blank its K, and the calibration median, the well tabs and their charts
  all recalculate - the same thing the app does when it screens a test out.
- The `Q virtual` column always holds the app's own numbers, so the workbook shows both what the app
  showed and what your K would give.
""")
