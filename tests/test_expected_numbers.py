"""Acceptance tests: the pipeline must reproduce the numbers in the build spec (within +/-2%)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core import config as C

REL = 0.02

ANALYSED = ["SA-0162_T", "SA-0500_T", "SA-0512H_T"]
EXCLUDED_WELL = "SA-0991H_T"

EXPECTED_K = {"SA-0162_T": 119.27, "SA-0500_T": 13.17, "SA-0512H_T": 22.14}
EXPECTED_SUSPECT = {("SA-0162_T", "2026-01-15", 539.0), ("SA-0500_T", "2026-04-26", 1761.0)}

# Spec table, over the 13 matched tests of the 3 analysed wells that have a leave-one-out value.
EXPECTED_MAPE = {
    "M1_LOO": (8.4, 3.8),
    "M2_WALK": (10.6, 5.0),
    "BASE_LAST_TEST": (13.7, 5.4),
}
# loaded for every well, including the excluded one
EXPECTED_PUMPS = {"SA-0162_T": ("D1150N", 157), "SA-0500_T": ("B538-1500", 148),
                  "SA-0512H_T": ("D1150N", 254), "SA-0991H_T": ("WG-4000", 145)}

ANALYSIS_TABLES = ["mapped", "matched", "cal", "validation", "mape", "mape_overall",
                   "daily", "hourly", "events", "pip_baselines", "sensitivity", "gas"]

# --- PVT addendum ---------------------------------------------------------------------------
EXPECTED_K_DH = {"SA-0162_T": 127.0, "SA-0500_T": 13.9, "SA-0512H_T": 23.0}
EXPECTED_MAPE_M6 = {"M6_LOO": (8.9, 4.1), "M6_WALK": (10.7, 5.1)}
EXPECTED_PB = {"SA-0162_T": 1535.0, "SA-0500_T": 1490.0, "SA-0512H_T": 1770.0}
# well -> (PIP - Pb median psi, % rows below Pb, GVF median %)
EXPECTED_GAS = {"SA-0162_T": (-96, 91, 1.0), "SA-0500_T": (61, 10, 0.0), "SA-0512H_T": (-1337, 100, 49)}


def _mentions(df: pd.DataFrame, well: str) -> bool:
    """True if `well` appears in any cell of `df`."""
    return any(df[c].astype(str).eq(well).any() for c in df.columns)


# --------------------------------------------------------------------------- well scope

def test_config_well_scope():
    assert C.WELLS == [w for w in C.WELLS_ALL if w not in C.EXCLUDED_WELLS]
    assert C.WELLS == ANALYSED
    assert C.is_excluded(EXCLUDED_WELL) and not C.is_excluded(ANALYSED[0])
    assert C.well_exclusion_reason(EXCLUDED_WELL).strip()
    assert C.well_exclusion_reason(ANALYSED[0]) == ""


def test_excluded_well_is_loaded(res):
    """It must be loaded from all three files and stay visible in the quality tables."""
    assert EXCLUDED_WELL in set(res.rt["WELL_NAME"])
    assert EXCLUDED_WELL in set(res.tests["WELL_NAME"])
    assert EXCLUDED_WELL in set(res.esp["WELL_NAME"])
    assert EXCLUDED_WELL in set(res.runs["WELL_NAME"])
    assert EXCLUDED_WELL in set(res.filter_summary["WELL_NAME"])
    assert EXCLUDED_WELL in set(res.monthly_flags["WELL_NAME"])
    assert int(res.filter_summary.set_index("WELL_NAME").loc[EXCLUDED_WELL, "rows"]) == 2725


def test_excluded_well_never_analysed(res):
    """Absent from every computed table, and carrying no K, rate or PHI."""
    for name in ANALYSIS_TABLES:
        assert not _mentions(getattr(res, name), EXCLUDED_WELL), f"{EXCLUDED_WELL} leaked into {name}"
    g = res.rt[res.rt["WELL_NAME"] == EXCLUDED_WELL]
    assert len(g) > 0
    assert not g["rate_ok"].any() and not g["rate_steady"].any()
    for c in ["K_single", "K_interp", "Q_single", "Q_interp", "PHI"]:
        assert g[c].isna().all(), c
    assert res.wells_analysed == ANALYSED
    assert res.wells_all == sorted(C.WELLS_ALL)


def test_excluded_well_listed_with_reason(res):
    ex = res.excluded.set_index("WELL_NAME")
    assert EXCLUDED_WELL in ex.index
    reason = ex.loc[EXCLUDED_WELL, "reason"]
    assert isinstance(reason, str) and reason.strip()
    assert reason == C.EXCLUDED_WELLS[EXCLUDED_WELL]        # verbatim from config
    assert ex.loc[EXCLUDED_WELL, "excluded_by"] == "config"
    assert ex.loc[EXCLUDED_WELL, "scada_rows"] == 2725
    assert ex.loc[EXCLUDED_WELL, "well_tests"] == 17


def test_excluded_well_name_not_hard_coded_outside_config():
    """Removing a well from EXCLUDED_WELLS must be the only change needed to analyse it."""
    root = Path(__file__).resolve().parent.parent
    files = [p for p in root.glob("core/*.py")] + [p for p in root.glob("ui/*.py")] \
        + [p for p in root.glob("app_pages/*.py")] + [root / "app.py"]
    offenders = [p.name for p in files
                 if p.name != "config.py" and EXCLUDED_WELL in p.read_text(encoding="utf-8")]
    assert offenders == [], f"well name hard-coded in {offenders}"


# --------------------------------------------------------------------------- dataset and flags

def test_dataset_shape(res):
    assert len(res.rt) == 69965                      # every SCADA row of every loaded well kept
    assert set(res.rt["WELL_NAME"]) == set(C.WELLS_ALL)
    assert len(res.tests) == 72                      # loaded
    assert res.meta["n_tests_analysed"] == 55        # on the analysed wells
    for _, g in res.rt.groupby("WELL_NAME"):
        assert g["TIME_STAMP"].is_monotonic_increasing


def test_no_rows_dropped_by_flags(res):
    d = res.rt
    assert d["usable"].sum() < len(d)                # exclusions exist ...
    assert len(d) == 69965                           # ... but rows are only flagged
    assert (d["steady"] <= d["usable"]).all()
    assert (d["rate_steady"] <= d["rate_ok"]).all()
    assert d.loc[~d["rate_ok"], "Q_interp"].isna().all()


# --------------------------------------------------------------------------- mapping and K

def test_thirteen_matched_tests(res):
    counts = res.mapped["MATCH"].value_counts()
    assert counts["MATCHED"] == 13
    assert len(res.matched) == 13
    assert len(res.mapped) == 55                     # analysed wells only
    assert counts.get("NO_RT_DATA", 0) + counts.get("INSUFFICIENT_STEADY_DATA", 0) == 55 - 13
    per_well = res.matched.groupby("WELL_NAME").size().to_dict()
    assert per_well == {"SA-0162_T": 4, "SA-0500_T": 5, "SA-0512H_T": 4}


@pytest.mark.parametrize("well,k_exp", list(EXPECTED_K.items()))
def test_k_single(res, well, k_exp):
    k = float(res.cal.set_index("WELL_NAME").loc[well, "K_single"])
    assert k == pytest.approx(k_exp, rel=REL), f"{well}: K_single={k:.2f} expected {k_exp}"


def test_suspect_tests(res):
    s = res.matched[res.matched["suspect"]]
    got = {(r["WELL_NAME"], r["TEST_TS"].strftime("%Y-%m-%d"), float(r["Q_LIQ"])) for _, r in s.iterrows()}
    assert got == EXPECTED_SUSPECT


def test_suspect_excluded_from_calibration(res):
    cal = res.cal.set_index("WELL_NAME")
    assert len(cal) == 3
    assert cal.loc["SA-0162_T", "n_tests"] == 3 and cal.loc["SA-0162_T", "n_suspect"] == 1
    assert cal.loc["SA-0500_T", "n_tests"] == 4 and cal.loc["SA-0500_T", "n_suspect"] == 1


# --------------------------------------------------------------------------- validation

@pytest.mark.parametrize("method,exp", list(EXPECTED_MAPE.items()))
def test_mape(res, method, exp):
    row = res.mape_overall.set_index("method").loc[method]
    assert row["MAPE_all"] == pytest.approx(exp[0], rel=REL), f"{method} all: {row['MAPE_all']:.2f} vs {exp[0]}"
    assert row["MAPE_excl_suspect"] == pytest.approx(exp[1], rel=REL), f"{method} excl: {row['MAPE_excl_suspect']:.2f} vs {exp[1]}"


def test_mape_common_test_set(res):
    row = res.mape_overall.set_index("method")
    for m in ["M1_LOO", "M6_LOO", "BASE_LAST_TEST"]:
        assert row.loc[m, "n_all"] == 13 and row.loc[m, "n_excl_suspect"] == 11, m
    for m in ["M2_WALK", "M6_WALK"]:                 # first test of each well has no earlier K
        assert row.loc[m, "n_all"] == 10 and row.loc[m, "n_excl_suspect"] == 8, m


def test_median_ape_reported(res):
    """MdAPE is reported next to every MAPE, overall and per well."""
    for t in (res.mape_overall, res.mape):
        for c in ["MAPE_all", "MdAPE_all", "MAPE_excl_suspect", "MdAPE_excl_suspect"]:
            assert c in t.columns
    row = res.mape_overall.set_index("method")
    # the two suspect tests pull every mean above the median
    for m in EXPECTED_MAPE:
        assert row.loc[m, "MdAPE_all"] < row.loc[m, "MAPE_all"]
    assert set(res.mape["scope"]) == {"ALL"} | set(ANALYSED)


def test_sensitivity_to_exclusion(res):
    """Including the excluded well adds no validated test; only dropping the common-set rule
    changes the baseline."""
    s = res.sensitivity
    common = s[s["test_rule"] == "Common test set"]
    analysed = common[common["wells_scope"].str.startswith("Analysed")].set_index("method")
    all_wells = common[common["wells_scope"].str.startswith("All")].set_index("method")
    for m in EXPECTED_MAPE:
        assert all_wells.loc[m, "MAPE_all"] == pytest.approx(analysed.loc[m, "MAPE_all"])
        assert all_wells.loc[m, "n_all"] == analysed.loc[m, "n_all"]
    loose = s[s["test_rule"] != "Common test set"].set_index("method")
    assert loose.loc["BASE_LAST_TEST", "n_all"] == 14                      # the extra single test
    assert loose.loc["BASE_LAST_TEST", "MAPE_all"] < analysed.loc["BASE_LAST_TEST", "MAPE_all"]
    assert loose.loc["M1_LOO", "MAPE_all"] == pytest.approx(analysed.loc["M1_LOO", "MAPE_all"])


def test_validation_counts(res):
    v = res.validation
    assert len(v) == 13
    assert v["APE_M1_LOO"].notna().sum() == 13       # every analysed well has >= 2 matched tests
    assert v["APE_M2_WALK"].notna().sum() == 10      # first test of each well has no earlier K
    assert v["APE_BASE_LAST_TEST"].notna().sum() == 13


# --------------------------------------------------------------------------- PVT: B_liq and M6

def test_b_liq_reproduces_the_file(res):
    """B_liq = WC * 1.020 + (1 - WC) * Bo must reproduce B_LIQ_RBSTB to 0.001."""
    from core.pvt import b_liq
    t = res.test_pvt.dropna(subset=["B_LIQ", "B_LIQ_FILE"])
    assert len(t) > 50
    assert (t["B_LIQ"] - t["B_LIQ_FILE"]).abs().max() < 0.001
    assert b_liq(1.0, 1.15) == pytest.approx(C.BW)        # all water
    assert b_liq(0.0, 1.15) == pytest.approx(1.15)        # all oil
    assert b_liq(0.5, 1.10) == pytest.approx(1.06)


def test_b_liq_on_matched_tests(res):
    mm = res.matched
    assert mm["B_LIQ"].notna().all() and mm["WC_FRAC"].between(0, 1).all()
    assert np.allclose(mm["K_dh"], mm["K"] * mm["B_LIQ"])


@pytest.mark.parametrize("well,k_exp", list(EXPECTED_K_DH.items()))
def test_k_dh_single(res, well, k_exp):
    k = float(res.cal.set_index("WELL_NAME").loc[well, "K_dh_single"])
    assert k == pytest.approx(k_exp, rel=REL), f"{well}: K_dh={k:.2f} expected {k_exp}"


@pytest.mark.parametrize("method,exp", list(EXPECTED_MAPE_M6.items()))
def test_mape_m6(res, method, exp):
    """The water-cut correction is physically right but slightly worse on this dataset."""
    row = res.mape_overall.set_index("method").loc[method]
    assert row["MAPE_all"] == pytest.approx(exp[0], rel=REL)
    assert row["MAPE_excl_suspect"] == pytest.approx(exp[1], rel=REL)


def test_m6_is_close_to_m1(res):
    row = res.mape_overall.set_index("method")
    assert row.loc["M6_LOO", "MAPE_excl_suspect"] > row.loc["M1_LOO", "MAPE_excl_suspect"]
    assert row.loc["M6_LOO", "MAPE_excl_suspect"] - row.loc["M1_LOO", "MAPE_excl_suspect"] < 1.0


def test_wc_correction_effect_is_small(res):
    """Within +/-2% on this dataset, as the toggle help text states."""
    d = res.rt[res.rt["rate_steady"]]
    for c in ["wc_effect_single_pct", "wc_effect_interp_pct"]:
        assert d[c].abs().max() < 2.5, c


def test_suspect_flag_does_not_depend_on_pvt(res):
    """A test is suspect on K, never on K_dh."""
    from core.calibration import robust_z
    mm = res.matched
    z_on_k = mm.groupby("WELL_NAME")["K"].transform(robust_z)
    assert (mm["suspect"] == (z_on_k > C.ROBUST_Z_MAX).fillna(False)).all()


# --------------------------------------------------------------------------- PVT: free gas

@pytest.mark.parametrize("well,pb", list(EXPECTED_PB.items()))
def test_bubble_point(res, well, pb):
    assert float(res.lab_pvt.set_index("WELL_NAME").loc[well, "PB"]) == pb


def test_lab_pvt_loaded_for_every_well(res):
    assert set(res.lab_pvt["WELL_NAME"]) == set(C.WELLS_ALL)
    assert res.lab_pvt["RESERVOIR_TEMP"].eq(173).all()


@pytest.mark.parametrize("well,exp", list(EXPECTED_GAS.items()))
def test_free_gas_indicator(res, well, exp):
    g = res.gas.set_index("WELL_NAME").loc[well]
    dpb, pct_below, gvf = exp
    assert g["PIP_minus_Pb_median"] == pytest.approx(dpb, abs=2)
    assert g["pct_rows_below_Pb"] == pytest.approx(pct_below, abs=1)
    assert g["gvf_median_pct"] == pytest.approx(gvf, abs=1.5)


def test_gassy_well_is_always_below_bubble_point(res):
    g = res.gas.set_index("WELL_NAME").loc["SA-0512H_T"]
    assert g["pct_rows_below_Pb"] == 100.0
    assert 45 <= g["gvf_p95_pct"] <= 60
    d = res.rt[(res.rt["WELL_NAME"] == "SA-0512H_T") & res.rt["rate_steady"]]
    assert d["below_pb"].all()


def test_gas_at_intake_event(res):
    e = res.events[res.events["type"] == "GAS_AT_INTAKE"]
    assert set(e["WELL_NAME"]) == {"SA-0512H_T"}      # the only well > 300 psi below Pb
    assert (e["severity"] == "info").all()
    assert "free gas at the pump lowers head" in e.iloc[0]["explanation"]
    assert C.GVF_CAVEAT in e.iloc[0]["evidence"]


# --------------------------------------------------------------------------- toggle OFF is inert

def test_wc_toggle_off_changes_nothing(res):
    """With the correction off every rate is the plain K * X, unchanged by the addendum."""
    from core.virtual_rate import period_stats, q_col
    assert q_col("interp") == "Q_interp" and q_col("single") == "Q_single"
    assert q_col("interp", True) == "Q_M6_interp" and q_col("single", True) == "Q_M6_single"
    d = res.rt[res.rt["rate_ok"]]
    assert np.allclose(d["Q_interp"], d["K_interp"] * d["X"])
    assert np.allclose(d["Q_single"], d["K_single"] * d["X"])
    assert np.allclose(d["Q_M6_interp"], d["K_dh_interp"] * d["X"] / d["B_LIQ"])
    w = res.rt[res.rt["WELL_NAME"] == ANALYSED[0]]
    off, on = period_stats(w, "interp"), period_stats(w, "interp", wc_correction=True)
    assert off["rate_median"] == pytest.approx(float(w[w["rate_steady"]]["Q_interp"].median()))
    assert on["rate_median"] == pytest.approx(float(w[w["rate_steady"]]["Q_M6_interp"].median()))
    assert off["cum_bbl"] != on["cum_bbl"]


# --------------------------------------------------------------------------- pump runs and events

@pytest.mark.parametrize("well,exp", list(EXPECTED_PUMPS.items()))
def test_pump_runs(res, well, exp):
    """Pump metadata is loaded for every well, excluded ones included."""
    r = res.runs.set_index("WELL_NAME").loc[well]
    assert (r["CANONICAL_MODEL"], int(r["NUMBER_OF_STAGES"])) == exp


def test_run_boundaries(res):
    inst = res.runs.set_index("WELL_NAME")["install_date"]
    assert inst["SA-0500_T"].strftime("%Y-%m") == "2023-05"
    assert inst["SA-0512H_T"].strftime("%Y-%m") == "2025-05"
    assert inst["SA-0162_T"].year == 2017
    assert (res.rt.loc[res.rt["WELL_NAME"] == "SA-0162_T", "run"] == "current").all()


def test_expected_events(res):
    e = res.events

    def has(well, etype, start, end):
        m = (e["WELL_NAME"] == well) & (e["type"] == etype) & (e["end"] >= start) & (e["start"] <= end)
        return bool(m.any())

    assert has("SA-0162_T", "BACKPRESSURE", "2025-10-15", "2025-12-01")
    assert has("SA-0162_T", "PHI_DRIFT", "2025-11-01", "2026-02-28")
    assert has("SA-0162_T", "SUSPECT_TEST", "2026-01-15", "2026-01-16")
    assert has("SA-0162_T", "VOLTAGE_BASIS_CHANGE", "2024-12-01", "2024-12-31")
    assert has("SA-0162_T", "TEMP_UNIT_SWITCH", "2025-09-01", "2025-11-01")
    assert has("SA-0512H_T", "VOLTAGE_STEP", "2025-08-01", "2025-08-31")
    assert has("SA-0500_T", "LOW_PIP_TREND", "2025-01-01", "2025-12-31")
    assert has("SA-0512H_T", "LOW_PIP_TREND", "2025-01-01", "2025-12-31")


# --------------------------------------------------------------------------- misc

def test_frequency_not_used_in_rate(res):
    """Q depends only on K, V, I, dP. FREQUENCY never enters it."""
    d = res.rt[res.rt["rate_ok"]].head(500)
    q = d["K_interp"] * np.sqrt(3) * d["VOLTAGE"] * d["AMPERAGE"] / (d["PDP"] - d["PIP"])
    assert np.allclose(q, d["Q_interp"])


def test_specific_gravity_column(res):
    """P55 fluid density is carried through as a specific gravity."""
    sg = res.tests["SG"].dropna()
    assert len(sg) > 0 and sg.between(0.85, 1.2).all()      # 7.1-10.0 ppg


def test_pipeline_fast_from_cache(res):
    import time
    from core.pipeline import run_pipeline
    t0 = time.time()
    run_pipeline()
    assert time.time() - t0 < 10.0
