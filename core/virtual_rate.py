"""Continuous virtual rate, PHI, resampling and period statistics (spec section 6).

Q_virtual = K * X with X = sqrt(3) * V * I / (PDP - PIP).
FREQUENCY is never used here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .calibration import k_interp
from .quality import add_elec_basis_flag

K_MODES = {"single": "Q_single", "interp": "Q_interp"}
SIGNAL_COLS = ["VOLTAGE", "AMPERAGE", "FREQ_FILLED", "FREQUENCY", "PIP", "PDP", "WHP", "dP",
               "MT_F", "INTAKE_TEMP_F", "X", "P_elec_kVA"]
RATE_COLS = ["Q_single", "Q_interp", "PHI", "K_single", "K_interp"]


def q_col(k_mode: str) -> str:
    return K_MODES[k_mode]


def compute_virtual_rate(d: pd.DataFrame, mm: pd.DataFrame, cal: pd.DataFrame) -> pd.DataFrame:
    """Add elec_basis_ok, K_single, K_interp, Q_single, Q_interp, PHI to the flagged frame.

    Rates are computed on rows that are `usable` and on the calibrated electrical basis
    (`rate_ok`). `rate_steady` additionally requires the row to be steady - the default
    selection for charts and statistics. All rows are kept.
    """
    d = add_elec_basis_flag(d, cal)
    d["K_single"] = np.nan
    d["K_interp"] = np.nan
    d["PHI"] = np.nan
    for _, r in cal.iterrows():
        w = r["WELL_NAME"]
        m = d["WELL_NAME"] == w
        d.loc[m, "K_single"] = r["K_single"]
        d.loc[m, "K_interp"] = k_interp(d.loc[m, "TIME_STAMP"], mm[mm["WELL_NAME"] == w])
        d.loc[m, "PHI"] = (d.loc[m, "dP"] / d.loc[m, "P_elec_kVA"]) / r["PHI_base"]
    d["rate_ok"] = d["usable"] & d["elec_basis_ok"] & np.isfinite(d["X"]) & d["K_single"].notna()
    d["rate_steady"] = d["rate_ok"] & d["steady"]
    d["Q_single"] = (d["K_single"] * d["X"]).where(d["rate_ok"])
    d["Q_interp"] = (d["K_interp"] * d["X"]).where(d["rate_ok"])
    d["PHI"] = d["PHI"].where(d["rate_ok"])
    return d


def rate_rows(d: pd.DataFrame, steady_only: bool = True) -> pd.DataFrame:
    """Rows carrying a virtual rate (steady by default)."""
    return d[d["rate_steady"]] if steady_only else d[d["rate_ok"]]


def resample_rates(d: pd.DataFrame, freq: str = "h", steady_only: bool = True,
                   keep_empty_bins: bool = False) -> pd.DataFrame:
    """Per-well median of rates and signals at `freq` ('30min', 'h', 'D').

    By default bins without data are dropped (compact tables). With `keep_empty_bins` they are
    kept as NaN so a line chart breaks at gaps instead of bridging them.
    """
    r = rate_rows(d, steady_only)
    cols = RATE_COLS + [c for c in SIGNAL_COLS if c in r.columns]
    if r.empty:
        return pd.DataFrame(columns=["WELL_NAME", "TIME_STAMP"] + cols + ["n_rows"])
    out = r.set_index("TIME_STAMP").groupby("WELL_NAME")[cols].resample(freq).median()
    n = r.set_index("TIME_STAMP").groupby("WELL_NAME")["Q_single"].resample(freq).size().rename("n_rows")
    out = out.join(n)
    if not keep_empty_bins:
        out = out.dropna(subset=["Q_single"], how="all")
    return out.reset_index()


def resample_signals(d: pd.DataFrame, freq: str = "h") -> pd.DataFrame:
    """Medians of the raw signals over ALL rows (pump-off included) plus the fraction of
    pump_off / usable / steady rows per bin. Empty bins are kept as NaN."""
    cols = [c for c in SIGNAL_COLS if c in d.columns]
    if d.empty:
        return pd.DataFrame(columns=["TIME_STAMP"] + cols + ["pump_off", "usable", "steady", "temp_unit_c", "n_rows"])
    gi = d.set_index("TIME_STAMP")
    out = gi[cols].resample(freq).median()
    for c in ["pump_off", "usable", "steady", "temp_unit_c"]:
        out[c] = gi[c].astype(float).resample(freq).mean()
    out["n_rows"] = gi["VOLTAGE"].resample(freq).size()
    return out.reset_index()


def daily_series(d: pd.DataFrame) -> pd.DataFrame:
    """Per-well calendar-day series used by diagnosis. Days without rows are present as NaN
    (except n_rows = 0) so consecutive-day logic works on a complete index.

    Signal medians are taken over rows that are not pump_off; rates/PHI over rate_steady rows.
    """
    frames = []
    for w, g in d.groupby("WELL_NAME", sort=False):
        gi = g.set_index("TIME_STAMP")
        day = gi.index.floor("D")
        on = gi[~gi["pump_off"]]
        rs = gi[gi["rate_steady"]]
        idx = pd.date_range(day.min(), day.max(), freq="D")
        out = pd.DataFrame(index=idx)
        out["n_rows"] = gi.groupby(day).size().reindex(idx).fillna(0).astype(int)
        out["n_pump_off"] = gi.groupby(day)["pump_off"].sum().reindex(idx).fillna(0).astype(int)
        out["n_usable"] = gi.groupby(day)["usable"].sum().reindex(idx).fillna(0).astype(int)
        out["n_rate"] = rs.groupby(rs.index.floor("D")).size().reindex(idx).fillna(0).astype(int)
        has_t = gi[gi["MT"].notna() | gi["INTAKE_TEMP"].notna()]
        out["temp_c_frac"] = has_t.groupby(has_t.index.floor("D"))["temp_unit_c"].mean().reindex(idx)
        for c in ["VOLTAGE", "AMPERAGE", "PIP", "PDP", "WHP", "dP", "MT_F", "FREQ_FILLED"]:
            out[c] = on.groupby(on.index.floor("D"))[c].median().reindex(idx)
        for c in ["Q_single", "Q_interp", "PHI", "K_interp"]:
            out[c] = rs.groupby(rs.index.floor("D"))[c].median().reindex(idx)
        if "run" in gi.columns:
            out["run"] = gi.groupby(day)["run"].agg(lambda x: x.mode().iloc[0]).reindex(idx)
        out["WELL_NAME"] = w
        out.index.name = "day"
        frames.append(out.reset_index())
    return pd.concat(frames, ignore_index=True)


def cumulative_liquid(d: pd.DataFrame, k_mode: str = "interp", steady_only: bool = True) -> tuple[float, float]:
    """(cumulative bbl, coverage %) from hourly medians: each hour with a valid rate
    contributes rate/24; hours without a valid rate contribute nothing."""
    r = rate_rows(d, steady_only)
    if r.empty:
        return 0.0, 0.0
    h = r.set_index("TIME_STAMP")[q_col(k_mode)].resample("h").median().dropna()
    span_h = max((r["TIME_STAMP"].max() - r["TIME_STAMP"].min()).total_seconds() / 3600.0, 1.0)
    return float(h.sum() / 24.0), float(min(len(h) / span_h * 100.0, 100.0))


def period_stats(d: pd.DataFrame, k_mode: str = "interp", steady_only: bool = True) -> dict:
    """Aggregate statistics for one well over an already-sliced frame `d`."""
    q = q_col(k_mode)
    r = rate_rows(d, steady_only)
    cum, cov = cumulative_liquid(d, k_mode, steady_only)
    n = len(d)
    phi = r["PHI"].dropna()
    out = dict(
        rows=n,
        rate_rows=int(len(r)),
        first=d["TIME_STAMP"].min() if n else pd.NaT,
        last=d["TIME_STAMP"].max() if n else pd.NaT,
        rate_mean=float(r[q].mean()) if len(r) else np.nan,
        rate_median=float(r[q].median()) if len(r) else np.nan,
        rate_p10=float(r[q].quantile(0.10)) if len(r) else np.nan,
        rate_p90=float(r[q].quantile(0.90)) if len(r) else np.nan,
        rate_last=float(r[q].iloc[-1]) if len(r) else np.nan,
        rate_last_ts=r["TIME_STAMP"].iloc[-1] if len(r) else pd.NaT,
        cum_bbl=cum,
        rate_coverage_pct=cov,
        uptime_pct=float((~d["pump_off"]).mean() * 100) if n else np.nan,
        quality_pct=float(d["usable"].mean() * 100) if n else np.nan,
        steady_pct=float(d["steady"].mean() * 100) if n else np.nan,
        basis_ok_pct=float((d["elec_basis_ok"] & d["usable"]).sum() / max(d["usable"].sum(), 1) * 100) if n else np.nan,
        phi_median=float(phi.median()) if len(phi) else np.nan,
        phi_min=float(phi.min()) if len(phi) else np.nan,
        phi_max=float(phi.max()) if len(phi) else np.nan,
        phi_last=float(phi.iloc[-1]) if len(phi) else np.nan,
        K=float(r["K_interp" if k_mode == "interp" else "K_single"].iloc[-1]) if len(r) else np.nan,
    )
    for c in ["VOLTAGE", "AMPERAGE", "FREQ_FILLED", "PIP", "PDP", "WHP", "dP", "MT_F"]:
        out["med_" + c] = float(r[c].median()) if len(r) else np.nan
    return out


def slice_period(d: pd.DataFrame, well: str, start, end) -> pd.DataFrame:
    """Rows of `well` with start <= TIME_STAMP < end + 1 day (end date inclusive)."""
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    if end.normalize() == end:
        end = end + pd.Timedelta(days=1)
    m = (d["WELL_NAME"] == well) & (d["TIME_STAMP"] >= start) & (d["TIME_STAMP"] < end)
    return d[m]


# --------------------------------------------------------------------------- intervals for chart shading

def _day_runs(days: pd.Series, mask: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """[(start_day, end_day_exclusive)] for consecutive True days."""
    out = []
    m = mask.to_numpy(dtype=bool)
    d = pd.to_datetime(days).to_numpy()
    start = None
    for i in range(len(m)):
        if m[i] and start is None:
            start = d[i]
        if start is not None and (not m[i] or i == len(m) - 1):
            end = d[i] if not m[i] else d[i] + np.timedelta64(1, "D")
            out.append((pd.Timestamp(start), pd.Timestamp(end)))
            start = None
    return out


def uncalibrated_intervals(daily: pd.DataFrame, d: pd.DataFrame, well: str) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Days where usable rows exist but most of them are off the calibrated voltage basis."""
    g = d[(d["WELL_NAME"] == well) & d["usable"]]
    if g.empty:
        return []
    frac = (~g["elec_basis_ok"]).groupby(g["TIME_STAMP"].dt.floor("D")).mean()
    dd = daily[daily["WELL_NAME"] == well]
    f = dd["day"].map(frac).fillna(0.0)
    return _day_runs(dd["day"], (f > 0.5) & (dd["n_usable"] > 0))


def gap_intervals(events: pd.DataFrame, well: str) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    e = events[(events["WELL_NAME"] == well) & (events["type"] == "SCADA_GAP")]
    return [(r["start"], r["end"]) for _, r in e.iterrows()]


def pump_off_intervals(events: pd.DataFrame, well: str) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    e = events[(events["WELL_NAME"] == well) & (events["type"] == "PUMP_OFF")]
    return [(r["start"], r["end"]) for _, r in e.iterrows()]


def rate_with_k(d: pd.DataFrame, k: float, steady_only: bool = True) -> pd.Series:
    """Virtual rate for an arbitrary K (what-if), on the rows carrying a rate."""
    r = rate_rows(d, steady_only)
    return k * r["X"]
