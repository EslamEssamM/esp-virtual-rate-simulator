"""Validation of the power method against well tests (spec section 5).

Three predictions per matched test:
  M1_LOO         K_single from the other non-suspect tests of the well x X at this test
  M2_WALK        K from the most recent earlier non-suspect test x X at this test
  BASE_LAST_TEST last well-test rate carried forward (what engineers use today)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

METHODS = {
    "M1_LOO": "M1 single-K (leave-one-out)",
    "M2_WALK": "M2 walk-forward K",
    "BASE_LAST_TEST": "Baseline: last test carried forward",
}


def validate(mm: pd.DataFrame, tests: pd.DataFrame) -> pd.DataFrame:
    """Per matched test: Q_TEST, the three predictions and their APE (%)."""
    res = []
    for w, g in mm.groupby("WELL_NAME"):
        g = g.sort_values("TEST_TS")
        all_tests = tests[tests["WELL_NAME"] == w].sort_values("TEST_TS")
        for i, r in g.iterrows():
            others = g[(g.index != i) & ~g["suspect"]]
            prev = g[(g["TEST_TS"] < r["TEST_TS"]) & ~g["suspect"]]
            prev_all = all_tests[all_tests["TEST_TS"] < r["TEST_TS"]]
            out = dict(WELL_NAME=w, TEST_TS=r["TEST_TS"], Q_TEST=r["Q_LIQ"], X=r["RT_X"], K=r["K"],
                       suspect=bool(r["suspect"]))
            out["Q_M1_LOO"] = others["K"].median() * r["RT_X"] if len(others) else np.nan
            out["Q_M2_WALK"] = prev["K"].iloc[-1] * r["RT_X"] if len(prev) else np.nan
            out["Q_BASE_LAST_TEST"] = prev_all["Q_LIQ"].iloc[-1] if len(prev_all) else np.nan
            out["BASE_TEST_TS"] = prev_all["TEST_TS"].iloc[-1] if len(prev_all) else pd.NaT
            res.append(out)
    v = pd.DataFrame(res)
    for mth in METHODS:
        v["APE_" + mth] = (v["Q_" + mth] - v["Q_TEST"]).abs() / v["Q_TEST"] * 100
    return v


def mape_table(v: pd.DataFrame) -> pd.DataFrame:
    """MAPE per method, overall and per well, with and without suspect tests."""
    rows = []
    scopes = [("ALL", v)] + [(w, g) for w, g in v.groupby("WELL_NAME")]
    for scope, g in scopes:
        for mth, label in METHODS.items():
            ape = g["APE_" + mth]
            ok = ape[~g["suspect"]]
            rows.append(dict(scope=scope, method=mth, label=label,
                             MAPE_all=float(ape.mean()) if ape.notna().any() else np.nan,
                             n_all=int(ape.notna().sum()),
                             MAPE_excl_suspect=float(ok.mean()) if ok.notna().any() else np.nan,
                             n_excl_suspect=int(ok.notna().sum())))
    return pd.DataFrame(rows)


def mape_summary(v: pd.DataFrame) -> pd.DataFrame:
    """Compact 3-row table (method x all / excl. suspect) for the overall scope."""
    t = mape_table(v)
    t = t[t["scope"] == "ALL"][["method", "label", "MAPE_all", "n_all", "MAPE_excl_suspect", "n_excl_suspect"]]
    return t.reset_index(drop=True)
