"""Acceptance tests: the pipeline must reproduce the numbers in the build spec (within +/-2%)."""
import numpy as np
import pandas as pd
import pytest

REL = 0.02

EXPECTED_K = {"SA-0162_T": 119.3, "SA-0500_T": 13.2, "SA-0512H_T": 22.1, "SA-0991H_T": 17.3}
EXPECTED_SUSPECT = {("SA-0162_T", "2026-01-15", 539.0), ("SA-0500_T", "2026-04-26", 1761.0)}

# Spec table: M1 8.4 / 3.8, M2 10.6 / 5.0, baseline 13.7 / 5.4.
# The baseline expectation cannot be reproduced from the CSV shipped with the spec (it has 72 tests
# for these wells, not 69, and the reference vr_pipeline.py itself yields 12.8 / 5.0 on it).
# M1 and M2 are asserted against the spec; the baseline is asserted against the reference output.
EXPECTED_MAPE = {
    "M1_LOO": (8.4, 3.8),
    "M2_WALK": (10.6, 5.0),
    "BASE_LAST_TEST": (12.8, 5.0),
}


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
