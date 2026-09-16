"""Validation of the power method against well tests (spec section 5).

Three predictions per matched test:
  M1_LOO         K_single from the other non-suspect tests of the well x X at this test
  M2_WALK        K from the most recent earlier non-suspect test x X at this test
  BASE_LAST_TEST last well-test rate carried forward (what engineers use today)

Errors are reported two ways for every scope:
  MAPE  = mean absolute percentage error   (sensitive to a single bad test)
  MdAPE = median absolute percentage error (the typical test)
and on two test sets: all matched tests, and excluding suspect tests.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

METHODS = {
    "M1_LOO": "M1 single-K (leave-one-out)",
    "M6_LOO": "M6 water-cut corrected (leave-one-out)",
    "M2_WALK": "M2 walk-forward K",
    "M6_WALK": "M6 water-cut corrected, walk-forward",
    "BASE_LAST_TEST": "Baseline: last test carried forward",
}
# methods that use the B_liq water-cut correction; shown next to their uncorrected twin
WC_METHODS = ["M6_LOO", "M6_WALK"]


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
            b = r.get("B_LIQ", np.nan)
            out["B_LIQ"] = b
            out["K_dh"] = r.get("K_dh", np.nan)
            out["Q_M1_LOO"] = others["K"].median() * r["RT_X"] if len(others) else np.nan
            out["Q_M2_WALK"] = prev["K"].iloc[-1] * r["RT_X"] if len(prev) else np.nan
            # M6: calibrate downhole, then convert back to surface at this test's B_liq
            out["Q_M6_LOO"] = (others["K_dh"].median() * r["RT_X"] / b
                               if len(others) and pd.notna(b) else np.nan)
            out["Q_M6_WALK"] = (prev["K_dh"].iloc[-1] * r["RT_X"] / b
                                if len(prev) and pd.notna(b) else np.nan)
            out["Q_BASE_LAST_TEST"] = prev_all["Q_LIQ"].iloc[-1] if len(prev_all) else np.nan
            out["BASE_TEST_TS"] = prev_all["TEST_TS"].iloc[-1] if len(prev_all) else pd.NaT
            res.append(out)
    v = pd.DataFrame(res)
    if v.empty:
        cols = ["WELL_NAME", "TEST_TS", "Q_TEST", "X", "K", "K_dh", "B_LIQ", "suspect", "BASE_TEST_TS"]
        return pd.DataFrame(columns=cols + [f"{p}_{m}" for m in METHODS for p in ("Q", "APE")])
    for mth in METHODS:
        v["APE_" + mth] = (v["Q_" + mth] - v["Q_TEST"]).abs() / v["Q_TEST"] * 100
    return v.sort_values(["WELL_NAME", "TEST_TS"]).reset_index(drop=True)


def common_test_set(v: pd.DataFrame) -> pd.DataFrame:
    """Tests on which every method is scored: those with an M1 leave-one-out value.

    A well with a single matched test has no leave-one-out prediction, so that test is dropped
    from every method's error to keep the three methods comparable on the same tests.
    """
    return v[v["APE_M1_LOO"].notna()]


def _stats(ape: pd.Series) -> tuple[float, float, int]:
    if ape.notna().sum() == 0:
        return np.nan, np.nan, 0
    return float(ape.mean()), float(ape.median()), int(ape.notna().sum())


def mape_table(v: pd.DataFrame, common_set: bool = True) -> pd.DataFrame:
    """MAPE and MdAPE per method, overall ('ALL') and per well, with and without suspect tests.

    With `common_set` (the default, and the documented rule) all methods are averaged over the
    tests that have a leave-one-out value.
    """
    if common_set:
        v = common_test_set(v)
    rows = []
    scopes = [("ALL", v)] + [(w, g) for w, g in v.groupby("WELL_NAME")]
    for scope, g in scopes:
        for mth, label in METHODS.items():
            ape = g["APE_" + mth]
            mean_all, med_all, n_all = _stats(ape)
            mean_ok, med_ok, n_ok = _stats(ape[~g["suspect"]])
            rows.append(dict(scope=scope, method=mth, label=label,
                             MAPE_all=mean_all, MdAPE_all=med_all, n_all=n_all,
                             MAPE_excl_suspect=mean_ok, MdAPE_excl_suspect=med_ok, n_excl_suspect=n_ok))
    return pd.DataFrame(rows)


def mape_summary(v: pd.DataFrame, common_set: bool = True) -> pd.DataFrame:
    """Compact 3-row table (one per method) for the overall scope."""
    t = mape_table(v, common_set)
    cols = ["method", "label", "MAPE_all", "MdAPE_all", "n_all",
            "MAPE_excl_suspect", "MdAPE_excl_suspect", "n_excl_suspect"]
    return t[t["scope"] == "ALL"][cols].reset_index(drop=True)


def scope_comparison(v_analysed: pd.DataFrame, v_all: pd.DataFrame,
                     n_analysed: int, n_all_wells: int) -> pd.DataFrame:
    """Sensitivity of the headline errors to the well-exclusion and test-set rules.

    Three rows per method:
      - analysed wells, common test set            (what the app reports everywhere)
      - all loaded wells, common test set          (excluded wells add no validated test)
      - all loaded wells, every test with a prediction (no common-set rule)
    The last row is the flattering number an unfiltered run would print; it is shown so the
    effect of both rules is visible, and it is never used anywhere else.
    """
    variants = [
        (f"Analysed wells ({n_analysed})", "Common test set", mape_summary(v_analysed, True)),
        (f"All loaded wells ({n_all_wells})", "Common test set", mape_summary(v_all, True)),
        (f"All loaded wells ({n_all_wells})", "Every test with a prediction", mape_summary(v_all, False)),
    ]
    out = []
    for wells_scope, rule, t in variants:
        t = t.copy()
        t.insert(0, "test_rule", rule)
        t.insert(0, "wells_scope", wells_scope)
        out.append(t)
    return pd.concat(out, ignore_index=True)


def last_test_summary(v: pd.DataFrame, mm: pd.DataFrame) -> pd.DataFrame:
    """Per well: the last matched test, its rate, and the walk-forward error the model made on it
    (M2; falls back to M1 leave-one-out when no earlier test exists)."""
    rows = []
    for w, g in v.groupby("WELL_NAME"):
        r = g.sort_values("TEST_TS").iloc[-1]
        ape, method = ((r["APE_M2_WALK"], "M2 walk-forward") if pd.notna(r["APE_M2_WALK"])
                       else (r["APE_M1_LOO"], "M1 leave-one-out"))
        rows.append(dict(WELL_NAME=w, last_test_ts=r["TEST_TS"], last_test_q=r["Q_TEST"],
                         last_test_suspect=bool(r["suspect"]), last_test_ape=ape,
                         last_test_method=method, n_matched=int(len(g))))
    return pd.DataFrame(rows)


def whatif_k(mm: pd.DataFrame, well: str, k: float) -> pd.DataFrame:
    """Predicted rate at each matched test of `well` for an arbitrary K, with APE."""
    g = mm[mm["WELL_NAME"] == well].sort_values("TEST_TS")
    out = g[["TEST_TS", "Q_LIQ", "RT_X", "K", "suspect"]].copy()
    out["Q_whatif"] = k * out["RT_X"]
    out["APE_whatif"] = (out["Q_whatif"] - out["Q_LIQ"]).abs() / out["Q_LIQ"] * 100
    return out.reset_index(drop=True)


def whatif_mape(t: pd.DataFrame) -> tuple[float, float]:
    """(MAPE all, MAPE excl. suspect) of a what-if table."""
    a = t["APE_whatif"].mean() if len(t) else np.nan
    b = t.loc[~t["suspect"], "APE_whatif"].mean() if (~t["suspect"]).any() else np.nan
    return float(a), float(b)
