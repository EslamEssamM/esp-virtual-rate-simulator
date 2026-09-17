"""Global sidebar controls; the chosen filters live in st.session_state['filters']."""
from __future__ import annotations

from dataclasses import asdict, fields

import streamlit as st

from core import config as C
from core import datasets as DS
from ui.data import FREQ_CODE, K_MODE_CODE, Scope, get_results


def _on_dataset_change():
    """Switching field drops the zoom and every cached period statistic.

    Widgets whose options depend on the dataset carry a dataset-suffixed key (see `wkey`), so
    they are separate widgets per field and cannot hold a value from the other one.
    """
    st.session_state.pop("zoom_window", None)
    st.cache_data.clear()


def rules() -> C.Thresholds:
    """The quality rules in force. The Filter rules page writes them; everything else reads them
    from here, so the whole app always runs on one rule set.

    Session state holds a plain dict of field values rather than the object: the app survives a
    code reload (which rebinds the class and would make an isinstance check fail) and an unknown
    field left over from an older version is ignored instead of raising.
    """
    d = st.session_state.get("rules")
    if not isinstance(d, dict):
        return C.DEFAULT_THRESHOLDS
    names = {f.name for f in fields(C.Thresholds)}
    return C.Thresholds(**{k: v for k, v in d.items() if k in names})


def set_rules(th: C.Thresholds):
    st.session_state["rules"] = asdict(th)


def pending_scope() -> Scope:
    """The scope the sidebar is about to select. The entry point warms this one, so a rule change
    does not run the pipeline twice - once on the previous scope and once on the new one."""
    return Scope(st.session_state.get("dataset") or DS.DEFAULT_DATASET, rules())


def wkey(name: str, dataset: str) -> str:
    """Widget key scoped to a dataset, for selections whose valid options change with the field."""
    return f"{name}__{dataset}"


def render_sidebar() -> dict:
    keys = DS.available()
    with st.sidebar:
        ds_key = st.selectbox("Dataset", keys, key="dataset", on_change=_on_dataset_change,
                              format_func=lambda k: DS.get(k).label,
                              help="Each field has its own loaders, wells and electrical basis. "
                                   "The physics and every downstream step are the same.")
    ds = DS.get(ds_key)
    scope = Scope(ds.key, rules())
    res = get_results(scope)
    t0, t1 = res.meta["rt_start"].date(), res.meta["rt_end"].date()
    dr_key, wells_key = wkey("date_range", ds.key), wkey("wells", ds.key)
    if dr_key not in st.session_state:
        st.session_state[dr_key] = (t0, t1)

    with st.sidebar:
        st.caption(ds.field_note)
        st.markdown("**Filters**")
        wells = st.multiselect("Wells", ds.wells_all, default=ds.wells, key=wells_key,
                               format_func=lambda w: f"{w}  (excluded)" if _excluded(res, w) else w,
                               help="Colours are fixed per well on every chart. Excluded wells are loaded and can be "
                                    "inspected on the Data quality page, but carry no rate, K or events.")
        excluded_now = _excluded_map(res)
        if excluded_now:
            with st.expander(f"Excluded wells ({len(excluded_now)})", icon=":material/block:"):
                for w, reason in excluded_now.items():
                    st.markdown(f"**{w}**")
                    st.caption(reason)
        picked = st.date_input("Date range", min_value=t0, max_value=t1, key=dr_key,
                               help="Inclusive of both days. Default is the full SCADA history.")
        if isinstance(picked, tuple) and len(picked) == 2:
            start, end = picked
        elif isinstance(picked, tuple) and len(picked) == 1:
            start, end = picked[0], t1
        else:
            start, end = t0, t1
        with st.container(horizontal=True):
            if st.button("Full range", icon=":material/restart_alt:", width="stretch"):
                st.session_state[dr_key] = (t0, t1)
                st.rerun()
        k_label = st.segmented_control("K mode", list(K_MODE_CODE), default="Interpolated K", key="k_mode",
                                       help="Interpolated: K linear in time between non-suspect tests, flat outside. "
                                            "Single: median K of the well's non-suspect tests.")
        freq_label = st.segmented_control("Resolution", list(FREQ_CODE), default="Daily", key="resolution",
                                          help="Hourly/daily are medians of the rows in each bin.")
        wc_help = C.WC_CORRECTION_HELP + ((" " + ds.wc_correction_note) if ds.wc_correction_note else "")
        wc_correction = st.toggle("Water-cut correction (B_liq)", value=False, key="wc_correction", help=wc_help)
        steady_only = st.toggle("Show only steady rows", value=True, key="steady_only",
                                help="Off: transient rows (rolling CV of I or dP > 5 %) are drawn too, in a lighter tint. "
                                     "K, MAPE and all statistics always use steady rows only.")
        show_analyst = False
        if len(res.analyst):
            show_analyst = st.toggle("Previous analyst series", value=False, key="show_analyst",
                                     help="Overlay the previous analyst's workbook rate, dashed. Their calibration "
                                          "factor is recomputed at every row against the allocated rate, so it cannot "
                                          "be validated. It is never used for calibration or MAPE here.")
        if not scope.rules.is_default:
            st.warning(f"{len(scope.rules.changes())} quality rule(s) changed from the default. "
                       "Every number in the app is computed under them.", icon=":material/rule_settings:")
            if st.button("Restore default rules", icon=":material/restart_alt:", width="stretch"):
                set_rules(C.DEFAULT_THRESHOLDS)
                st.rerun()
        n_an, n_all = len(res.wells_analysed), len(res.wells_all)
        st.caption(f"SCADA {t0:%d %b %Y} to {t1:%d %b %Y} - {res.meta['n_rows']:,} rows over {n_all} wells, "
                   f"{n_an} analysed. {res.meta['n_tests_analysed']} well tests on the analysed wells, "
                   f"{res.meta['n_matched']} matched. "
                   + ("Loaded from parquet cache." if res.meta.get("from_cache") else "Processed from source and cached."))

    selected = [w for w in ds.wells_all if w in set(wells)]
    f = dict(dataset=ds.key, dataset_label=ds.label, scope=scope, rules=scope.rules,
             wells=tuple(selected),
             wells_analysed=tuple(w for w in selected if not _excluded(res, w)),
             wells_excluded=tuple(w for w in selected if _excluded(res, w)),
             start=str(start), end=str(end),
             k_mode=K_MODE_CODE.get(k_label or "Interpolated K", "interp"),
             freq=FREQ_CODE.get(freq_label or "Daily", "D"),
             steady_only=bool(steady_only), wc_correction=bool(wc_correction),
             show_analyst=bool(show_analyst), t0=t0, t1=t1)
    st.session_state["filters"] = f
    return f


def _excluded_map(res) -> dict:
    """Excluded wells for this dataset, including any auto-excluded for too few matched tests."""
    if not len(res.excluded):
        return {}
    return dict(zip(res.excluded["WELL_NAME"], res.excluded["reason"]))


def _excluded(res, well: str) -> bool:
    return well in _excluded_map(res)


def filters() -> dict:
    return st.session_state.get("filters") or render_sidebar()
