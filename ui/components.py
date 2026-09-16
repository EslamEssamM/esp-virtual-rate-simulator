"""Reusable page pieces: well header with pump badges, KPI tiles, dataset notes."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import config as C
from core.config import PHI_HELP
from ui.theme import fmt_num

BASE_NOTES = [
    "All wells have a SCADA gap from Jun to Nov 2024 (shaded grey on the charts).",
    "The VOLTAGE tag changes basis over time (LV drive side vs MV motor side). K is only valid on the basis it was "
    "calibrated on, so periods on another basis are shown as 'uncalibrated' (amber shading) and carry no rate.",
    "SA-0162_T temperatures are logged in degC before Oct-2025 and degF after; both are converted to degF for display.",
    "Implied overall efficiency (K x 1000 / 78818) is 0.17-0.28 on the MV wells, lower than the 0.5-0.7 expected for "
    "PF x eta_m x eta_p. Most likely a tag-basis issue; it is shown as a caveat and deliberately not corrected.",
    "FREQUENCY is null in about half of the rows and is never used in the rate. It is shown filled (forward-fill up to "
    "24 h, then monthly median) for display only.",
]


def dataset_notes_list() -> list[str]:
    """Known dataset facts, with one line per excluded well generated from the config."""
    notes = [f"{w} is loaded but excluded from the analysis: {reason}" for w, reason in C.EXCLUDED_WELLS.items()]
    return notes + BASE_NOTES


def dataset_notes(expanded: bool = False):
    with st.expander("Known dataset facts", icon=":material/info:", expanded=expanded):
        for n in dataset_notes_list():
            st.markdown(f"- {n}")


def excluded_banner(well: str, where: str = "analysis"):
    """Banner shown wherever an excluded well would otherwise carry numbers."""
    st.warning(f"**{well} is excluded from {where}.** {C.well_exclusion_reason(well)}", icon=":material/block:")


def excluded_notice(wells, where: str = "analysis") -> bool:
    """Banner for each selected excluded well. Returns True when at least one was shown."""
    shown = False
    for w in wells:
        excluded_banner(w, where)
        shown = True
    return shown


def well_header(well: str, run: dict, extra: str | None = None):
    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader(well, anchor=False)
        if run:
            model = run.get("CANONICAL_MODEL", "")
            stages = run.get("NUMBER_OF_STAGES")
            st.badge(f"{model} x{int(stages)} stages" if pd.notna(stages) else model, icon=":material/settings_input_component:", color="violet")
            if run.get("PUMP_MANUFACTURER"):
                st.badge(run["PUMP_MANUFACTURER"], color="gray")
            if pd.notna(run.get("INSTALL_TOP_DEPTH_FT")):
                st.badge(f"{run['INSTALL_TOP_DEPTH_FT']:,.0f} ft TVD", icon=":material/vertical_align_bottom:", color="gray")
            if pd.notna(run.get("install_date")):
                st.badge(f"run since {pd.Timestamp(run['install_date']):%b %Y}", icon=":material/history:", color="blue")
        if extra:
            st.caption(extra)


def kpi_tiles(stats: dict, cal_row: pd.Series | None, last: pd.Series | None, k_mode: str, run_label_now: str):
    """Seven KPI tiles for one well over the selected period."""
    med = stats["rate_median"]
    last_rate = stats["rate_last"]
    delta_rate = (f"{last_rate - med:+,.0f} vs period median" if pd.notna(last_rate) and pd.notna(med) else None)
    k_label = "K interpolated" if k_mode == "interp" else "K single"
    c = st.columns(4, gap="small") + st.columns(4, gap="small")
    with c[0]:
        st.metric("Virtual rate, BFPD", fmt_num(last_rate, 0), delta_rate, delta_color="off", border=True,
                  help=f"Last steady row in the selected period ({stats['rate_last_ts']:%Y-%m-%d %H:%M})." if pd.notna(stats["rate_last_ts"]) else "No steady rows with a calibrated rate in this period.")
    with c[1]:
        st.metric(k_label, fmt_num(stats["K"], 2), border=True,
                  help="Q = K x sqrt(3) x V x I / (PDP - PIP). K lumps PF, motor and pump efficiency, "
                       "transformer/cable ratios and the volume factor. "
                       + (f"K single = {cal_row['K_single']:.2f} from {int(cal_row['n_tests'])} tests" if cal_row is not None else ""))
    with c[2]:
        if last is not None:
            st.metric("Last test, BFPD", fmt_num(last["last_test_q"], 0),
                      f"{pd.Timestamp(last['last_test_ts']):%d %b %Y}" + (" (suspect)" if last["last_test_suspect"] else ""),
                      delta_color="off", delta_arrow="off", border=True,
                      help="Most recent well test that has steady SCADA rows within +/-12 h (or +/-24 h).")
        else:
            st.metric("Last test, BFPD", "none", border=True)
    with c[3]:
        if last is not None:
            st.metric("Last test error, %", fmt_num(last["last_test_ape"], 1), border=True,
                      help=f"Absolute % error of the {last['last_test_method']} prediction on the last matched test, i.e. what the model said before that test was known.")
        else:
            st.metric("Last test error, %", "n/a", border=True)
    with c[4]:
        phi = stats["phi_last"]
        st.metric("PHI (last)", fmt_num(phi, 3), (f"{(phi - 1) * 100:+.1f} % vs calib." if pd.notna(phi) else None),
                  delta_color="off", border=True, help=PHI_HELP)
    with c[5]:
        st.metric("Data quality, %", fmt_num(stats["quality_pct"], 0), border=True,
                  help="Usable rows / all rows in the period (no missing V/I/PIP/PDP, pump on, dP > 300 psi, pressures and Hz in range).")
    with c[6]:
        st.metric("Uptime, %", fmt_num(stats["uptime_pct"], 0), border=True,
                  help="Rows not flagged pump_off (V >= 100 V, I >= 5 A, Hz != 0) / all rows in the period.")
    with c[7]:
        st.metric("Metered liquid, bbl", fmt_num(stats["cum_bbl"], 0), f"{stats['rate_coverage_pct']:.0f} % of hours metered",
                  delta_color="off", delta_arrow="off", border=True,
                  help="Sum of hourly median virtual rate x 1 h over hours that have a valid steady rate. Hours without a rate "
                       "contribute nothing, so this is metered liquid, not calendar production.")
    if run_label_now == "previous":
        st.caption(":material/history: The selected period ends inside the **previous pump run** for this well. "
                   "K is calibrated on the current run's tests; treat rates in the previous run as indicative only.")
