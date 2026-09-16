"""Cached access to pipeline results and cheap per-filter slices for the pages."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from core import virtual_rate as vr
from core.diagnosis import events_in_window
from core.pipeline import Results, run_pipeline
from core.validation import last_test_summary


@st.cache_resource(show_spinner="Loading SCADA data, well tests and running the power-method pipeline...")
def get_results() -> Results:
    """The whole pipeline, computed once per server process (parquet-cached on disk)."""
    return run_pipeline()


@st.cache_resource
def get_last_tests() -> pd.DataFrame:
    r = get_results()
    return last_test_summary(r.validation, r.matched)


def _ts(x) -> pd.Timestamp:
    return pd.Timestamp(x)


@st.cache_data(show_spinner=False, max_entries=64)
def well_slice(well: str, start: str, end: str) -> pd.DataFrame:
    """All rows of one well inside [start, end] (end date inclusive)."""
    return vr.slice_period(get_results().rt, well, _ts(start), _ts(end))


@st.cache_data(show_spinner=False, max_entries=64)
def rate_series(well: str, start: str, end: str, freq: str, steady_only: bool) -> pd.DataFrame:
    """Rate/PHI series for charts. '30min' -> rows (with a rate_steady flag and NaN breaks at
    gaps > 2 h); 'h' / 'D' -> medians with empty bins kept so lines break at gaps."""
    d = well_slice(well, start, end)
    if freq == "30min":
        r = vr.rate_rows(d, steady_only)[["TIME_STAMP", "Q_single", "Q_interp", "PHI", "K_single", "K_interp",
                                          "X", "rate_steady", "VOLTAGE", "AMPERAGE", "dP", "PIP", "PDP", "WHP"]]
        return break_gaps(r, pd.Timedelta(hours=2))
    return vr.resample_rates(d, freq, steady_only, keep_empty_bins=True)


@st.cache_data(show_spinner=False, max_entries=64)
def signal_series(well: str, start: str, end: str, freq: str) -> pd.DataFrame:
    """Raw signals over ALL rows (including pump-off) for the signal viewer."""
    d = well_slice(well, start, end)
    if freq == "30min":
        cols = ["TIME_STAMP"] + [c for c in vr.SIGNAL_COLS if c in d.columns] + ["pump_off", "usable", "steady", "temp_unit_c"]
        return break_gaps(d[cols], pd.Timedelta(hours=2))
    return vr.resample_signals(d, freq)


@st.cache_data(show_spinner=False, max_entries=128)
def period_stats(well: str, start: str, end: str, k_mode: str, steady_only: bool) -> dict:
    return vr.period_stats(well_slice(well, start, end), k_mode, steady_only)


@st.cache_data(show_spinner=False, max_entries=64)
def window_events(wells: tuple[str, ...], start: str, end: str) -> pd.DataFrame:
    return events_in_window(get_results().events, wells, _ts(start), _ts(end))


@st.cache_data(show_spinner=False, max_entries=64)
def shading(well: str) -> dict:
    """Intervals used to shade the rate charts: SCADA gaps, uncalibrated voltage basis, previous run."""
    r = get_results()
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
                run=runs.loc[well].to_dict() if well in runs.index else {})


def break_gaps(df: pd.DataFrame, max_gap: pd.Timedelta) -> pd.DataFrame:
    """Insert a NaN row after every time step larger than `max_gap` so Plotly breaks the line."""
    if df.empty:
        return df
    df = df.sort_values("TIME_STAMP").reset_index(drop=True)
    gap_idx = np.flatnonzero((df["TIME_STAMP"].diff() > max_gap).to_numpy())
    if len(gap_idx) == 0:
        return df
    filler = df.iloc[gap_idx - 1].copy()
    filler["TIME_STAMP"] = filler["TIME_STAMP"] + pd.Timedelta(minutes=1)
    for c in filler.columns:
        if c != "TIME_STAMP":
            filler[c] = np.nan if filler[c].dtype.kind in "fiu" else filler[c]
    out = pd.concat([df, filler]).sort_values("TIME_STAMP", kind="mergesort").reset_index(drop=True)
    return out


def tests_in_range(well: str, start: str, end: str) -> pd.DataFrame:
    r = get_results()
    t = r.mapped[(r.mapped["WELL_NAME"] == well) & (r.mapped["TEST_TS"] >= _ts(start)) & (r.mapped["TEST_TS"] < _ts(end) + pd.Timedelta(days=1))]
    m = r.matched[["WELL_NAME", "TEST_TS", "suspect", "robust_z"]]
    return t.merge(m, on=["WELL_NAME", "TEST_TS"], how="left")


FREQ_CODE = {"30-min": "30min", "Hourly": "h", "Daily": "D"}
FREQ_LABEL = {"30min": "30-min rows", "h": "hourly median", "D": "daily median"}
K_MODE_CODE = {"Interpolated K": "interp", "Single K": "single"}
