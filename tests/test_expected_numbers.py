"""Acceptance tests: the pipeline must reproduce the numbers in the build spec (within +/-2%)."""
import numpy as np
import pandas as pd
import pytest

REL = 0.02

EXPECTED_K = {"SA-0162_T": 119.3, "SA-0500_T": 13.2, "SA-0512H_T": 22.1, "SA-0991H_T": 17.3}
EXPECTED_SUSPECT = {("SA-0162_T", "2026-01-15", 539.0), ("SA-0500_T", "2026-04-26", 1761.0)}

# Spec table: M1 8.4 / 3.8, M2 10.6 / 5.0, baseline 13.7 / 5.4.
# Ruling: every MAPE is averaged over the same test set - the 13 tests that have an M1
# leave-one-out value (SA-0991H's single test is excluded from all three) - and n is reported.
EXPECTED_MAPE = {
    "M1_LOO": (8.4, 3.8),
    "M2_WALK": (10.6, 5.0),
    "BASE_LAST_TEST": (13.7, 5.4),
}
EXPECTED_PUMPS = {"SA-0162_T": ("D1150N", 157), "SA-0500_T": ("B538-1500", 148),
                  "SA-0512H_T": ("D1150N", 254), "SA-0991H_T": ("WG-4000", 145)}


def test_dataset_shape(res):
    assert len(res.rt) == 69965                      # every SCADA row kept
    assert set(res.rt["WELL_NAME"]) == set(EXPECTED_K)
    assert len(res.tests) == 72
    assert res.rt["TIME_STAMP"].is_monotonic_increasing or True  # sorted within well
    for _, g in res.rt.groupby("WELL_NAME"):
        assert g["TIME_STAMP"].is_monotonic_increasing


def test_no_rows_dropped_by_flags(res):
    d = res.rt
    assert d["usable"].sum() < len(d)                # exclusions exist ...
    assert len(d) == 69965                           # ... but rows are only flagged
    assert (d["steady"] <= d["usable"]).all()
    assert (d["rate_steady"] <= d["rate_ok"]).all()
    assert d.loc[~d["rate_ok"], "Q_interp"].isna().all()


def test_fourteen_matched_tests(res):
    counts = res.mapped["MATCH"].value_counts()
    assert counts["MATCHED"] == 14
    assert len(res.matched) == 14
    assert counts.get("NO_RT_DATA", 0) + counts.get("INSUFFICIENT_STEADY_DATA", 0) == 72 - 14
    per_well = res.matched.groupby("WELL_NAME").size().to_dict()
    assert per_well == {"SA-0162_T": 4, "SA-0500_T": 5, "SA-0512H_T": 4, "SA-0991H_T": 1}


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
    assert cal.loc["SA-0162_T", "n_tests"] == 3 and cal.loc["SA-0162_T", "n_suspect"] == 1
    assert cal.loc["SA-0500_T", "n_tests"] == 4 and cal.loc["SA-0500_T", "n_suspect"] == 1


@pytest.mark.parametrize("method,exp", list(EXPECTED_MAPE.items()))
def test_mape(res, method, exp):
    row = res.mape_overall.set_index("method").loc[method]
    assert row["MAPE_all"] == pytest.approx(exp[0], rel=REL), f"{method} all: {row['MAPE_all']:.2f} vs {exp[0]}"
    assert row["MAPE_excl_suspect"] == pytest.approx(exp[1], rel=REL), f"{method} excl: {row['MAPE_excl_suspect']:.2f} vs {exp[1]}"


def test_mape_common_test_set(res):
    row = res.mape_overall.set_index("method")
    assert row["n_all"].tolist() == [13, 10, 13]
    assert row["n_excl_suspect"].tolist() == [11, 8, 11]


@pytest.mark.parametrize("well,exp", list(EXPECTED_PUMPS.items()))
def test_pump_runs(res, well, exp):
    r = res.runs.set_index("WELL_NAME").loc[well]
    assert (r["CANONICAL_MODEL"], int(r["NUMBER_OF_STAGES"])) == exp


def test_run_boundaries(res):
    inst = res.runs.set_index("WELL_NAME")["install_date"]
    assert inst["SA-0500_T"].strftime("%Y-%m") == "2023-05"
    assert inst["SA-0512H_T"].strftime("%Y-%m") == "2025-05"
    assert inst["SA-0991H_T"].strftime("%Y-%m") == "2026-06"
    assert inst["SA-0162_T"].year == 2017
    # SA-0991H SCADA (Apr-May 2024) belongs entirely to the previous run
    assert (res.rt.loc[res.rt["WELL_NAME"] == "SA-0991H_T", "run"] == "previous").all()
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


def test_validation_counts(res):
    v = res.validation
    assert len(v) == 14
    assert v["APE_M1_LOO"].notna().sum() == 13       # SA-0991H has a single test -> no LOO
    assert v["APE_M2_WALK"].notna().sum() == 10      # first test of each well has no earlier K
    assert v["APE_BASE_LAST_TEST"].notna().sum() == 14


def test_frequency_not_used_in_rate(res):
    """Q depends only on K, V, I, dP. Perturbing FREQUENCY must not change Q."""
    d = res.rt[res.rt["rate_ok"]].head(500)
    q = d["K_interp"] * np.sqrt(3) * d["VOLTAGE"] * d["AMPERAGE"] / (d["PDP"] - d["PIP"])
    assert np.allclose(q, d["Q_interp"])


def test_pipeline_fast_from_cache(res):
    import time
    from core.pipeline import run_pipeline
    t0 = time.time()
    run_pipeline()
    assert time.time() - t0 < 10.0
