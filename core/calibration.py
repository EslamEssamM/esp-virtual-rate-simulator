"""Per-well calibration factor K (spec section 4).

K_single : median K of the well's matched, non-suspect tests.
K_interp : K linearly interpolated in time between non-suspect tests,
           flat before the first and after the last.
Suspect  : robust z-score |K - median| / (1.4826 * MAD) > 3.5 within the well.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C


def robust_z(k: pd.Series) -> pd.Series:
    """|k - median| / (1.4826 * MAD). NaN where MAD is zero (cannot judge)."""
    med = k.median()
    mad = (k - med).abs().median() * C.MAD_SCALE
    if not np.isfinite(mad) or mad == 0:
        return pd.Series(np.nan, index=k.index)
    return (k - med).abs() / mad


def flag_suspect(m: pd.DataFrame, z_max: float = C.ROBUST_Z_MAX) -> pd.DataFrame:
    """Matched tests only, with `robust_z` and boolean `suspect` columns."""
    mm = m[m["MATCH"] == "MATCHED"].copy()
    mm["robust_z"] = mm.groupby("WELL_NAME")["K"].transform(robust_z)
    mm["suspect"] = (mm["robust_z"] > z_max).fillna(False).astype(bool)
    return mm.sort_values(["WELL_NAME", "TEST_TS"]).reset_index(drop=True)


def calibration_table(mm: pd.DataFrame) -> pd.DataFrame:
    """One row per well: K_single, spread, test count/date range, voltage basis window
    and the PHI calibration baseline."""
    good = mm[~mm["suspect"]]
    rows = []
    for w, g in good.groupby("WELL_NAME"):
        k = g["K"]
        rows.append(dict(
            WELL_NAME=w,
            K_single=float(k.median()),
            K_min=float(k.min()), K_max=float(k.max()),
            K_cv_pct=float(k.std() / k.mean() * 100) if len(k) > 1 else np.nan,
            n_tests=int(len(g)),
            n_suspect=int(mm[(mm["WELL_NAME"] == w) & mm["suspect"]].shape[0]),
            first_test=g["TEST_TS"].min(), last_test=g["TEST_TS"].max(),
            V_lo=float(C.ELEC_BASIS_LO * g["RT_VOLTAGE"].min()),
            V_hi=float(C.ELEC_BASIS_HI * g["RT_VOLTAGE"].max()),
            V_BASIS=g["V_BASIS"].mode().iloc[0],
            PHI_base=float(np.median(g["RT_dP"] / g["RT_P_elec_kVA"])),
            # PIP baseline for LOW_PIP_TREND = median SCADA PIP around the FIRST calibration test
            PIP_base=float(g.sort_values("TEST_TS")["RT_PIP"].iloc[0]),
            PIP_base_median=float(g["RT_PIP"].median()),
            implied_eff=float(k.median() * 1000.0 / C.EFF_DENOM),
        ))
    return pd.DataFrame(rows)


def pip_baselines(mm: pd.DataFrame, runs: pd.DataFrame | None, rt_start, rt_end) -> pd.DataFrame:
    """One row per (well, pump run): run_start/run_end, PIP_base = median SCADA PIP at the first
    non-suspect matched test inside that run, and base_test_ts. Runs come from the ESP master
    dataset (install date of the current run); without it, the whole history is one run."""
    rows = []
    far_past, far_future = pd.Timestamp(rt_start) - pd.Timedelta(days=1), pd.Timestamp(rt_end) + pd.Timedelta(days=1)
    for w, g in mm[~mm["suspect"]].groupby("WELL_NAME"):
        inst = pd.NaT
        if runs is not None and w in runs["WELL_NAME"].values:
            inst = runs.set_index("WELL_NAME").loc[w, "install_date"]
        bounds = [("previous", far_past, inst), ("current", inst, far_future)] if pd.notna(inst) else [("current", far_past, far_future)]
        for run, a, b in bounds:
            t = g[(g["TEST_TS"] >= a) & (g["TEST_TS"] < b)].sort_values("TEST_TS")
            rows.append(dict(WELL_NAME=w, run=run, run_start=a, run_end=b,
                             PIP_base=float(t["RT_PIP"].iloc[0]) if len(t) else np.nan,
                             base_test_ts=t["TEST_TS"].iloc[0] if len(t) else pd.NaT, n_tests=int(len(t))))
    return pd.DataFrame(rows)


def k_interp(times: pd.Series, cal_tests: pd.DataFrame) -> np.ndarray:
    """K at each timestamp, linear between non-suspect tests, flat outside."""
    c = cal_tests[~cal_tests["suspect"]].sort_values("TEST_TS")
    if c.empty:
        return np.full(len(times), np.nan)
    tnum = pd.to_datetime(times).astype("datetime64[ns]").astype("int64").to_numpy()
    cnum = c["TEST_TS"].astype("datetime64[ns]").astype("int64").to_numpy()
    return np.interp(tnum, cnum, c["K"].to_numpy())


def calibrate(m: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convenience: (matched tests with suspect flag, per-well calibration table)."""
    mm = flag_suspect(m)
    return mm, calibration_table(mm)
