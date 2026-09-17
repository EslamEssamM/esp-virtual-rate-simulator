"""Streamlit wrapper around `core.export_excel`: cached workbook bytes for the download button.

The workbook itself is built by pure code in core/; this module only supplies the display maps
(well colours, category and method labels, dataset notes) and keeps the result in the cache so
the download button does not rebuild the file on every rerun.
"""
from __future__ import annotations

import streamlit as st

from core import export_excel as XL
from ui.components import dataset_notes_list
from ui.data import Scope, get_results
from ui.theme import CATEGORY_LABELS, METHOD_LABELS, well_color


def _options(res, scope: Scope, wells: tuple[str, ...], **kw) -> XL.ExportOptions:
    return XL.ExportOptions(
        wells=tuple(wells),
        well_colors={w: well_color(w) for w in wells},
        category_labels=dict(CATEGORY_LABELS),
        method_labels=dict(METHOD_LABELS),
        notes=tuple(dataset_notes_list(res)),
        rule_changes=tuple(scope.rules.changes()),
        **kw,
    )


# Only two workbooks are kept: a 30-minute export of a full history is tens of megabytes, and
# this cache lives in the server process alongside the pipeline results.
@st.cache_data(show_spinner=False, max_entries=2)
def workbook(scope: Scope, wells: tuple[str, ...], start: str, end: str, k_mode: str, freq: str,
             steady_only: bool, wc_correction: bool, live_formulas: bool, per_well_tabs: bool,
             include_events: bool, include_quality: bool, include_pvt: bool) -> bytes:
    """The .xlsx as bytes. Cached on the selection, so re-downloading is instant."""
    res = get_results(scope)
    opt = _options(res, scope, wells, start=start, end=end, k_mode=k_mode, freq=freq,
                   steady_only=steady_only, wc_correction=wc_correction,
                   live_formulas=live_formulas, per_well_tabs=per_well_tabs,
                   include_events=include_events, include_quality=include_quality,
                   include_pvt=include_pvt)
    return XL.workbook_bytes(res, opt)


@st.cache_data(show_spinner=False, max_entries=16)
def series_rows(scope: Scope, wells: tuple[str, ...], start: str, end: str, freq: str,
                steady_only: bool) -> int:
    """Rows the per-well tabs will hold, so the page can warn before a slow 30-minute export."""
    res = get_results(scope)
    opt = _options(res, scope, wells, start=start, end=end, freq=freq, steady_only=steady_only)
    return XL.estimated_rows(res, opt)


def filename(scope: Scope, start: str, end: str) -> str:
    res = get_results(scope)
    name = XL.suggested_filename(res, XL.ExportOptions(start=start, end=end))
    # a workbook built under changed rules must not be mistaken for the published numbers
    return name if scope.rules.is_default else name.replace(".xlsx", f"_rules-{scope.rules.key}.xlsx")
