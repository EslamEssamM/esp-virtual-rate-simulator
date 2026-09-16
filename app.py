"""ESP Virtual Rate Simulator - Streamlit entry point.

All physics lives in core/; this file only wires the sidebar and the pages together.
Run with:  streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="ESP Virtual Rate Simulator", page_icon=":material/oil_barrel:",
                   layout="wide", initial_sidebar_state="expanded")

from ui.data import get_results  # noqa: E402
from ui.sidebar import render_sidebar  # noqa: E402

pages = [
    st.Page("app_pages/overview.py", title="Overview", icon=":material/dashboard:", default=True),
    st.Page("app_pages/data_quality.py", title="Data quality", icon=":material/rule:", url_path="data-quality"),
    st.Page("app_pages/calibration.py", title="Calibration & validation", icon=":material/tune:", url_path="calibration"),
    st.Page("app_pages/methodology.py", title="Methodology", icon=":material/functions:", url_path="methodology"),
]
page = st.navigation(pages, position="sidebar")

with st.sidebar:
    st.title("ESP Virtual Rate", icon=":material/oil_barrel:")
    st.caption("Camilleri power-equilibrium method, one calibration factor K per well.")

get_results()          # first call runs the pipeline (with a spinner); later calls are instant
render_sidebar()
page.run()
