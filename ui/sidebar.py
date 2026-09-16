"""Global sidebar controls; the chosen filters live in st.session_state['filters']."""
from __future__ import annotations

import streamlit as st

from core import config as C
from ui.data import FREQ_CODE, K_MODE_CODE, get_results


def _well_label(w: str) -> str:
    return f"{w}  (excluded)" if C.is_excluded(w) else w


def render_sidebar() -> dict:
    res = get_results()
    t0, t1 = res.meta["rt_start"].date(), res.meta["rt_end"].date()
    if "date_range" not in st.session_state:
        st.session_state["date_range"] = (t0, t1)

    with st.sidebar:
        st.markdown("**Filters**")
        wells = st.multiselect("Wells", C.WELLS_ALL, default=C.WELLS, key="wells",
                               format_func=_well_label,
                               help="Colours are fixed per well on every chart. Excluded wells are loaded and can be "
                                    "inspected on the Data quality page, but carry no rate, K or events.")
        if C.EXCLUDED_WELLS:
            with st.expander(f"Excluded wells ({len(C.EXCLUDED_WELLS)})", icon=":material/block:"):
                for w, reason in C.EXCLUDED_WELLS.items():
                    st.markdown(f"**{w}**")
                    st.caption(reason)
        picked = st.date_input("Date range", min_value=t0, max_value=t1, key="date_range",
                               help="Inclusive of both days. Default is the full SCADA history.")
        if isinstance(picked, tuple) and len(picked) == 2:
            start, end = picked
        elif isinstance(picked, tuple) and len(picked) == 1:
            start, end = picked[0], t1
        else:
            start, end = t0, t1
        with st.container(horizontal=True):
            if st.button("Full range", icon=":material/restart_alt:", width="stretch"):
                st.session_state["date_range"] = (t0, t1)
                st.rerun()
        k_label = st.segmented_control("K mode", list(K_MODE_CODE), default="Interpolated K", key="k_mode",
                                       help="Interpolated: K linear in time between non-suspect tests, flat outside. "
                                            "Single: median K of the well's non-suspect tests.")
        freq_label = st.segmented_control("Resolution", list(FREQ_CODE), default="Daily", key="resolution",
                                          help="Hourly/daily are medians of the rows in each bin.")
        steady_only = st.toggle("Show only steady rows", value=True, key="steady_only",
                                help="Off: transient rows (rolling CV of I or dP > 5 %) are drawn too, in a lighter tint. "
                                     "K, MAPE and all statistics always use steady rows only.")
        n_an, n_all = len(res.wells_analysed), len(res.wells_all)
        st.caption(f"SCADA {t0:%d %b %Y} to {t1:%d %b %Y} - {res.meta['n_rows']:,} rows over {n_all} wells, "
                   f"{n_an} analysed. {res.meta['n_tests_analysed']} well tests on the analysed wells, "
                   f"{res.meta['n_matched']} matched. "
                   + ("Loaded from parquet cache." if res.meta.get("from_cache") else "Processed from Excel and cached."))

    selected = [w for w in C.WELLS_ALL if w in set(wells)]
    f = dict(wells=tuple(selected),
             wells_analysed=tuple(w for w in selected if not C.is_excluded(w)),
             wells_excluded=tuple(w for w in selected if C.is_excluded(w)),
             start=str(start), end=str(end),
             k_mode=K_MODE_CODE.get(k_label or "Interpolated K", "interp"),
             freq=FREQ_CODE.get(freq_label or "Daily", "D"),
             steady_only=bool(steady_only), t0=t0, t1=t1)
    st.session_state["filters"] = f
    return f


def filters() -> dict:
    return st.session_state.get("filters") or render_sidebar()
