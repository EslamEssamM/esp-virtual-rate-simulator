"""Well test <-> SCADA mapping (spec section 3)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

RT_MEDIAN_COLS = ["VOLTAGE", "AMPERAGE", "FREQ_FILLED", "PIP", "PDP", "WHP", "dP", "X", "P_elec_kVA", "MT_F"]


def map_tests(d: pd.DataFrame, t: pd.DataFrame,
              win_h: int = C.MAP_WINDOW_H, wide_h: int = C.MAP_WINDOW_WIDE_H,
              min_rows: int = C.MAP_MIN_ROWS) -> pd.DataFrame:
    """One row per well test with the median steady SCADA state around the test.

    MATCH is 'MATCHED', 'NO_RT_DATA' (no SCADA rows in the widest window) or
    'INSUFFICIENT_STEADY_DATA'. K = Q_test / X is filled for matched tests only.
    """
    rows = []
    by_well = {w: g for w, g in d.groupby("WELL_NAME", sort=False)}
    for _, r in t.iterrows():
        g = by_well.get(r["WELL_NAME"])
        rec = r.to_dict()
        if g is None or g.empty:
            rec.update(window_h=wide_h, n_window=0, n_steady=0, nearest_rt_h=np.nan, MATCH="NO_RT_DATA")
            rows.append(rec)
            continue
        ts = g["TIME_STAMP"]
        w = s = g.iloc[0:0]
        win = win_h
        for win in (win_h, wide_h):
            w = g[(ts >= r["TEST_TS"] - pd.Timedelta(hours=win)) & (ts <= r["TEST_TS"] + pd.Timedelta(hours=win))]
            s = w[w["steady"]]
            if len(s) >= min_rows:
                break
        near = (ts - r["TEST_TS"]).abs().min()
        rec.update(window_h=win, n_window=int(len(w)), n_steady=int(len(s)),
                   nearest_rt_h=near.total_seconds() / 3600.0)
        if len(s) >= min_rows:
            for c in RT_MEDIAN_COLS:
                rec["RT_" + c] = float(s[c].median())
            rec["V_BASIS"] = s["V_BASIS"].mode().iloc[0]
            t_pip = r["T_PIP"]
            rec["PIP_diff_vs_test"] = rec["RT_PIP"] - t_pip if pd.notna(t_pip) and t_pip > 0 else np.nan
            rec["MATCH"] = "MATCHED"
        else:
            rec["MATCH"] = "NO_RT_DATA" if len(w) == 0 else "INSUFFICIENT_STEADY_DATA"
        rows.append(rec)
    m = pd.DataFrame(rows)
    for c in ["RT_" + c for c in RT_MEDIAN_COLS] + ["PIP_diff_vs_test"]:
        if c not in m.columns:
            m[c] = np.nan
    if "V_BASIS" not in m.columns:
        m["V_BASIS"] = None
    m["K"] = m["Q_LIQ"] / m["RT_X"]
    return m
