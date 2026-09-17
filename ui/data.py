"""Cached access to pipeline results and cheap per-filter slices for the pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dataclasses import dataclass

from core import config as C
from core import datasets as DS
from core import virtual_rate as vr
from core.diagnosis import events_in_window
from core.pipeline import Results, run_pipeline
from core.virtual_rate import break_gaps
from core.validation import last_test_summary


@dataclass(frozen=True)
class Scope:
    """Which dataset, under which quality rules.

    Every cached helper below takes a Scope as its first argument, so two datasets - or the same
    dataset under two rule sets - can never share a cache entry, and switching either one is just
    a different key. Frozen and made of plain values, so Streamlit can hash it.
    """
    dataset: str = DS.DEFAULT_DATASET
    rules: C.Thresholds = C.DEFAULT_THRESHOLDS

    @property
    def label(self) -> str:
        return DS.get(self.dataset).label

    def with_defaults(self) -> "Scope":
        """The same dataset under the default rules, for before/after comparisons."""
        return Scope(self.dataset)


@st.cache_resource(show_spinner="Loading SCADA data, well tests and running the power-method pipeline...")
def get_results(scope: Scope = Scope()) -> Results:
    """The whole pipeline for one dataset and rule set, computed once per server process."""
    return run_pipeline(scope.dataset, rules=scope.rules)


@st.cache_resource
def get_last_tests(scope: Scope = Scope()) -> pd.DataFrame:
    r = get_results(scope)
    return last_test_summary(r.validation, r.matched)


def _ts(x) -> pd.Timestamp:
    return pd.Timestamp(x)


@st.cache_data(show_spinner=False, max_entries=64)
def well_slice(scope: Scope, well: str, start: str, end: str) -> pd.DataFrame:
    """All rows of one well inside [start, end] (end date inclusive)."""
    return vr.slice_period(get_results(scope).rt, well, _ts(start), _ts(end))


@st.cache_data(show_spinner=False, max_entries=64)
def rate_series(scope: Scope, well: str, start: str, end: str, freq: str, steady_only: bool) -> pd.DataFrame:
    """Rate/PHI series for charts. '30min' -> rows (with a rate_steady flag and NaN breaks at
    gaps > 2 h); 'h' / 'D' -> medians with empty bins kept so lines break at gaps."""
    d = well_slice(scope, well, start, end)
    if freq == "30min":
        cols = (["TIME_STAMP", "X", "rate_steady", "VOLTAGE", "AMPERAGE", "dP", "PIP", "PDP", "WHP"]
                + [c for c in vr.RATE_COLS if c in d.columns]
                + [c for c in vr.PVT_ROW_COLS if c in d.columns])
        r = vr.rate_rows(d, steady_only)[cols]
        return break_gaps(r, pd.Timedelta(hours=2))
    return vr.resample_rates(d, freq, steady_only, keep_empty_bins=True)


@st.cache_data(show_spinner=False, max_entries=64)
def signal_series(scope: Scope, well: str, start: str, end: str, freq: str) -> pd.DataFrame:
    """Raw signals over ALL rows (including pump-off) for the signal viewer."""
    d = well_slice(scope, well, start, end)
    if freq == "30min":
        cols = (["TIME_STAMP"] + [c for c in vr.SIGNAL_COLS + vr.PVT_ROW_COLS + ["Pb"] if c in d.columns]
                + ["pump_off", "usable", "steady", "temp_unit_c"])
        return break_gaps(d[cols], pd.Timedelta(hours=2))
    return vr.resample_signals(d, freq)


@st.cache_data(show_spinner=False, max_entries=128)
def period_stats(scope: Scope, well: str, start: str, end: str, k_mode: str, steady_only: bool,
                 wc_correction: bool = False) -> dict:
    return vr.period_stats(well_slice(scope, well, start, end), k_mode, steady_only, wc_correction)


def cal_rows(cal: pd.DataFrame, well: str) -> pd.DataFrame:
    """Every calibration row of a well, one per electrical regime, oldest first."""
    if not len(cal) or "WELL_NAME" not in cal.columns:
        return cal
    g = cal[cal["WELL_NAME"] == well]
    return g.sort_values("regime") if "regime" in g.columns else g


def cal_row(cal: pd.DataFrame, well: str, regime: int | None = None) -> pd.Series | None:
    """One calibration row: the given regime, or the well's latest if not specified."""
    g = cal_rows(cal, well)
    if not len(g):
        return None
    if regime is not None and "regime" in g.columns and (g["regime"] == regime).any():
        return g[g["regime"] == regime].iloc[-1]
    return g.iloc[-1]


@st.cache_resource
def mape_by_well(scope: Scope = Scope()) -> pd.DataFrame:
    """Per-well MAPE/MdAPE indexed by (well, method)."""
    return get_results(scope).mape.set_index(["scope", "method"])


@st.cache_resource
def gas_by_well(scope: Scope = Scope()) -> pd.DataFrame:
    """Per-well intake-vs-bubble-point summary, indexed by well. Empty without a bubble point."""
    g = get_results(scope).gas
    return g.set_index("WELL_NAME") if len(g) else g


@st.cache_data(show_spinner=False, max_entries=16)
def pvt_series(scope: Scope, well: str) -> pd.DataFrame:
    """Per-test water cut and B_liq for the water-cut chart."""
    t = get_results(scope).test_pvt
    if not len(t):
        return t
    return t[t["WELL_NAME"] == well].sort_values("TEST_TS")


@st.cache_data(show_spinner=False, max_entries=16)
def regime_marks(scope: Scope, well: str) -> list:
    """Timestamps where a well changes electrical regime, for vertical markers."""
    r = get_results(scope).regimes
    g = r[r["WELL_NAME"] == well].sort_values("regime")
    return [pd.Timestamp(t) for t in g["start"].iloc[1:]] if len(g) > 1 else []


@st.cache_data(show_spinner=False, max_entries=16)
def analyst_series(scope: Scope, well: str, start: str, end: str) -> pd.DataFrame:
    """The previous analyst's workbook series for one well, for optional overlay only."""
    a = get_results(scope).analyst
    if not len(a):
        return a
    m = ((a["WELL_NAME"] == well) & (a["TIME_STAMP"] >= _ts(start))
         & (a["TIME_STAMP"] < _ts(end) + pd.Timedelta(days=1)))
    return a[m]


@st.cache_data(show_spinner=False, max_entries=64)
def window_events(scope: Scope, wells: tuple[str, ...], start: str, end: str) -> pd.DataFrame:
    return events_in_window(get_results(scope).events, wells, _ts(start), _ts(end))


@st.cache_data(show_spinner=False, max_entries=64)
def shading(scope: Scope, well: str) -> dict:
    """Intervals used to shade the rate charts: SCADA gaps, uncalibrated voltage basis, previous run."""
    r = get_results(scope)
    runs = r.runs.set_index("WELL_NAME")
    inst = runs.loc[well, "install_date"] if well in runs.index else pd.NaT
    d = r.rt[r.rt["WELL_NAME"] == well]
    prev = []
    if pd.notna(inst) and len(d) and d["TIME_STAMP"].min() < inst:
        prev = [(d["TIME_STAMP"].min(), min(inst, d["TIME_STAMP"].max()))]
    return dict(gaps=vr.gap_intervals(r.events, well),
                uncalibrated=vr.uncalibrated_intervals(r.daily, r.rt, well),
                previous_run=prev,
                install_date=inst,
                run=runs.loc[well].to_dict() if well in runs.index else {},
                regimes=regime_marks(scope, well))


def tests_in_range(scope: Scope, well: str, start: str, end: str) -> pd.DataFrame:
    r = get_results(scope)
    t = r.mapped[(r.mapped["WELL_NAME"] == well) & (r.mapped["TEST_TS"] >= _ts(start)) & (r.mapped["TEST_TS"] < _ts(end) + pd.Timedelta(days=1))]
    m = r.matched[["WELL_NAME", "TEST_TS", "suspect", "robust_z"]]
    return t.merge(m, on=["WELL_NAME", "TEST_TS"], how="left")


FREQ_CODE = {"30-min": "30min", "Hourly": "h", "Daily": "D"}
FREQ_LABEL = {"30min": "30-min rows", "h": "hourly median", "D": "daily median"}
K_MODE_CODE = {"Interpolated K": "interp", "Single K": "single"}
