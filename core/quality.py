"""Row-level quality flags and derived signals (spec section 2).

Every row is kept. Flags are boolean columns; nothing is dropped here or anywhere
else in the core - downstream steps *select* rows by flag, and the Data-quality page
shows every exclusion.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

FLAG_COLUMNS = [
    "missing_elec", "missing_press", "pump_off", "bad_dP", "bad_press_range",
    "bad_freq", "transient", "temp_unit_c", "gauge_frozen",
]
EXCLUSION_FLAGS = ["missing_elec", "missing_press", "pump_off", "bad_dP", "bad_press_range", "bad_freq"]


def _rolling_cv(s: pd.Series, th: C.Thresholds) -> pd.Series:
    r = s.rolling(th.transient_window, min_periods=th.transient_min_periods)
    return r.std() / r.mean().abs()


def add_quality_flags(d: pd.DataFrame, th: C.Thresholds = C.DEFAULT_THRESHOLDS) -> pd.DataFrame:
    """Return a copy of the SCADA frame with flags and derived columns added.

    Expects the frame sorted by (WELL_NAME, TIME_STAMP) as produced by load.load_rt.
    `th` carries the thresholds; the defaults are the ones the README and the tests pin down,
    and the Filter rules page passes a user-built set instead.
    """
    d = d.copy()
    V, I, F = d["VOLTAGE"], d["AMPERAGE"], d["FREQUENCY"]
    dP = d["PDP"] - d["PIP"]

    # --- flags -------------------------------------------------------------
    d["missing_elec"] = V.isna() | I.isna()            # X cannot be formed without V and I
    d["missing_press"] = d["PIP"].isna() | d["PDP"].isna()
    d["pump_off"] = (V < th.pump_off_v) | (I < th.pump_off_i) | (F == 0)
    # A zero floor switches the rule off, but dP must stay strictly positive whatever the
    # setting: X = sqrt(3)*V*I/dP is infinite at dP = 0 and negative below it.
    d["bad_dP"] = ~(dP > max(th.min_dp, 0.0))            # also true when dP is NaN
    # PDP carries no ceiling by default: discharge pressure varies too much between fields to
    # bound sensibly, and a bad reading usually fails the dP rule or the PIP range instead. A
    # gauge pegged at its rail is the exception - it holds a plausible dP - so the ceiling is
    # available as a rule the user sets per field.
    d["bad_press_range"] = ~d["PIP"].between(th.pip_lo, th.pip_hi)
    if th.pdp_max:
        d["bad_press_range"] |= d["PDP"] >= th.pdp_max
    if th.whp_over_pdp:
        d["bad_press_range"] |= d["WHP"] > d["PDP"]
    d["bad_freq"] = (F.notna() & (F != 0) & ~F.between(th.freq_lo, th.freq_hi)
                     if th.freq_gate else pd.Series(False, index=d.index))

    g_well = d.groupby("WELL_NAME", sort=False)
    amp_cv = g_well["AMPERAGE"].transform(_rolling_cv, th)
    dp_cv = dP.groupby(d["WELL_NAME"], sort=False).transform(_rolling_cv, th)
    d["transient"] = ((amp_cv > th.transient_cv) | (dp_cv > th.transient_cv)
                      if th.transient_gate else pd.Series(False, index=d.index))
    d["amp_cv"] = amp_cv
    d["dP_cv"] = dp_cv

    # --- temperature units: some wells logged degC for part of the history ----
    d["temp_unit_c"] = (d["MT"] < C.TEMP_C_MT_MAX) | (d["INTAKE_TEMP"] < C.TEMP_C_IT_MAX)
    d["MT_F"] = np.where(d["temp_unit_c"], d["MT"] * 9 / 5 + 32, d["MT"])
    d["INTAKE_TEMP_F"] = np.where(d["temp_unit_c"], d["INTAKE_TEMP"] * 9 / 5 + 32, d["INTAKE_TEMP"])

    # --- derived signals ---------------------------------------------------
    d["dP"] = dP
    d["P_elec_kVA"] = np.sqrt(3) * V * I / 1000.0
    d["X"] = np.sqrt(3) * V * I / dP
    d["V_BASIS"] = np.where(V < C.LV_MV_SPLIT_V, "LV", "MV")

    d["gauge_frozen"] = False        # datasets that ship their own flag overwrite this
    d["usable"] = ~d[EXCLUSION_FLAGS].any(axis=1)
    d["steady"] = d["usable"] & ~d["transient"]

    # --- FREQUENCY for display only: ffill <= 24 h, then monthly median ----
    d["FREQ_FILLED"] = _fill_frequency(d)
    return d


def _fill_frequency(d: pd.DataFrame) -> pd.Series:
    """Forward-fill FREQUENCY within 24 h of the last reading, then fall back to the
    well's monthly median. Zero readings (pump off) are kept as zero. Display only."""
    out = pd.Series(np.nan, index=d.index, dtype="float64")
    limit = pd.Timedelta(hours=C.FREQ_FFILL_LIMIT_H)
    for _, g in d.groupby("WELL_NAME", sort=False):
        f = g["FREQUENCY"]
        ts = g["TIME_STAMP"]
        last_ts = ts.where(f.notna()).ffill()
        ff = f.ffill()
        ff = ff.where((ts - last_ts) <= limit)
        month = ts.dt.to_period("M")
        monthly = f.where(f > 0).groupby(month).transform("median")
        out.loc[g.index] = ff.fillna(monthly)
    return out


def add_elec_basis_flag(d: pd.DataFrame, cal: pd.DataFrame) -> pd.DataFrame:
    """`elec_basis_ok`: VOLTAGE within [0.8 x min, 1.2 x max] of the voltages seen in the
    well's (non-suspect) calibration tests. Wells without calibration -> False."""
    d = d.copy()
    ok = pd.Series(False, index=d.index)
    for _, r in cal.iterrows():
        m = d["WELL_NAME"] == r["WELL_NAME"]
        if "regime" in d.columns and "regime" in cal.columns:
            m &= d["regime"] == r["regime"]
        ok.loc[m] = d.loc[m, "VOLTAGE"].between(r["V_lo"], r["V_hi"])
    d["elec_basis_ok"] = ok
    return d


def filter_summary(d: pd.DataFrame) -> pd.DataFrame:
    """Per-well row counts for every flag, plus usable/steady counts and percentages."""
    g = d.groupby("WELL_NAME")
    s = g[FLAG_COLUMNS].sum().astype(int)
    s.insert(0, "rows", g.size())
    s["usable"] = g["usable"].sum().astype(int)
    s["steady"] = g["steady"].sum().astype(int)
    if "elec_basis_ok" in d.columns:
        s["calibrated_basis"] = (d["steady"] & d["elec_basis_ok"]).groupby(d["WELL_NAME"]).sum().astype(int)
    s["usable_pct"] = (s["usable"] / s["rows"] * 100).round(1)
    s["steady_pct"] = (s["steady"] / s["rows"] * 100).round(1)
    s["first"] = g["TIME_STAMP"].min()
    s["last"] = g["TIME_STAMP"].max()
    return s.reset_index()


def exclusion_reason(d: pd.DataFrame) -> pd.Series:
    """First exclusion reason per row, in priority order; 'steady' / 'transient' for usable rows."""
    reason = pd.Series("steady", index=d.index, dtype="object")
    reason[d["transient"] & d["usable"]] = "transient"
    for f in reversed(EXCLUSION_FLAGS):        # earlier flags win
        reason[d[f]] = f
    if "gauge_frozen" in d.columns:
        reason[d["gauge_frozen"].astype(bool)] = "gauge_frozen"
    if "elec_basis_ok" in d.columns:
        reason[(reason == "steady") & ~d["elec_basis_ok"]] = "uncalibrated_basis"
    return reason


def monthly_flag_counts(d: pd.DataFrame) -> pd.DataFrame:
    """Long table (WELL_NAME, month, category, rows) for the stacked bar chart."""
    cat = exclusion_reason(d)
    month = d["TIME_STAMP"].dt.to_period("M").dt.to_timestamp()
    out = (pd.DataFrame({"WELL_NAME": d["WELL_NAME"], "month": month, "category": cat})
           .groupby(["WELL_NAME", "month", "category"]).size().rename("rows").reset_index())
    return out
