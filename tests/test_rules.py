"""User-set quality rules must reach every step of the pipeline, and the defaults must not move.

The dP cases double as the record of why the 300 psi floor is kept as the default rather than
removed: on GC31 it does nothing, on Meleiha it is rejecting a frozen gauge.
"""
import numpy as np
import pandas as pd
import pytest

from core import config as C
from core.pipeline import run_pipeline
from core.quality import add_quality_flags


def _rt(n=40, well="W1", start="2025-01-01", V=2000.0, I=30.0, PIP=1000.0, PDP=3000.0, freq=50.0):
    ts = pd.date_range(start, periods=n, freq="30min")
    return pd.DataFrame(dict(WELL_NAME=well, TIME_STAMP=ts, WHP=200.0, WHT=np.nan, FLP=150.0,
                             PIP=PIP, PDP=PDP, INTAKE_TEMP=170.0, MT=230.0, FREQUENCY=freq,
                             VOLTAGE=V, AMPERAGE=I))


# --------------------------------------------------------------------------- the object itself

def test_defaults_match_the_module_constants():
    t = C.DEFAULT_THRESHOLDS
    assert t.is_default and t.key == ""
    assert (t.min_dp, t.pip_lo, t.pip_hi) == (C.MIN_DP, *C.PIP_RANGE)
    assert (t.freq_lo, t.freq_hi) == C.FREQ_RANGE
    assert (t.transient_window, t.transient_cv) == (C.TRANSIENT_WINDOW, C.TRANSIENT_CV)
    assert (t.elec_basis_lo, t.elec_basis_hi) == (C.ELEC_BASIS_LO, C.ELEC_BASIS_HI)


def test_changes_are_reported_and_keyed():
    t = C.Thresholds(min_dp=0.0, transient_gate=False)
    assert not t.is_default and len(t.key) == 8
    labels = {lab for lab, _, _ in t.changes()}
    assert labels == {"dP floor", "Apply the transient rule"}
    assert C.Thresholds(min_dp=0.0).key != C.Thresholds(min_dp=100.0).key
    assert hash(t) == hash(C.Thresholds(min_dp=0.0, transient_gate=False))


# --------------------------------------------------------------------------- row flags

def test_dp_floor_is_applied_and_can_be_switched_off():
    d = _rt()
    d.loc[0, "PDP"] = 1200.0                       # dP = 200: under the default floor
    d.loc[1, "PDP"] = 1000.0                       # dP = 0: never acceptable
    d.loc[2, "PDP"] = 900.0                        # dP < 0: never acceptable
    assert add_quality_flags(d)["bad_dP"].iloc[:3].tolist() == [True, True, True]
    off = add_quality_flags(d, C.Thresholds(min_dp=0.0))["bad_dP"]
    # a zero floor admits the 200 psi row but still rejects dP <= 0, because X = k / dP
    assert off.iloc[:3].tolist() == [False, True, True]


def test_each_gate_can_be_turned_off():
    d = _rt()
    d.loc[0, "FREQUENCY"] = 20.0
    d.loc[1, "WHP"] = 5000.0                       # WHP > PDP
    assert add_quality_flags(d)["bad_freq"].iloc[0]
    assert not add_quality_flags(d, C.Thresholds(freq_gate=False))["bad_freq"].any()
    assert add_quality_flags(d)["bad_press_range"].iloc[1]
    assert not add_quality_flags(d, C.Thresholds(whp_over_pdp=False))["bad_press_range"].iloc[1]


def test_transient_gate_off_makes_steady_equal_usable():
    d = _rt()
    d.loc[10:14, "AMPERAGE"] = [30, 60, 20, 55, 25]      # a burst of transients
    base = add_quality_flags(d)
    assert base["transient"].any()
    off = add_quality_flags(d, C.Thresholds(transient_gate=False))
    assert not off["transient"].any()
    assert off["steady"].equals(off["usable"])


def test_pump_off_and_pip_range_follow_the_rules():
    d = _rt(V=150.0, I=8.0, PIP=40.0)
    assert not add_quality_flags(d)["pump_off"].any()
    assert add_quality_flags(d, C.Thresholds(pump_off_v=200.0))["pump_off"].all()
    assert add_quality_flags(d)["bad_press_range"].all()          # PIP 40 < default 50
    assert not add_quality_flags(d, C.Thresholds(pip_lo=10.0))["bad_press_range"].any()


# --------------------------------------------------------------------------- through the pipeline

def test_default_rules_reproduce_the_published_numbers(res):
    assert res.meta["rules_default"] and res.meta["rules_key"] == ""
    again = run_pipeline("GC31", rules=C.DEFAULT_THRESHOLDS)
    pd.testing.assert_frame_equal(res.cal, again.cal)


def test_rules_reach_calibration_and_are_recorded():
    """The calibrated voltage window is a rule, so widening it must widen the window in `cal`."""
    base = run_pipeline("GC31")
    wide = run_pipeline("GC31", rules=C.Thresholds(elec_basis_lo=0.5, elec_basis_hi=1.5))
    assert wide.meta["rules_key"] and not wide.meta["rules_default"]
    assert (wide.cal["V_lo"] < base.cal["V_lo"]).all()
    assert (wide.cal["V_hi"] > base.cal["V_hi"]).all()
    # a wider window admits rows on an electrical basis K was never calibrated on
    assert wide.rt["rate_steady"].sum() > base.rt["rate_steady"].sum()


def test_dropping_the_dp_floor_changes_nothing_on_gc31(res):
    """On GC31 every row the floor rejects is already rejected for a missing pressure or a
    stopped pump, so the rule is doing no work: removing it moves no K and no error."""
    off = run_pipeline("GC31", rules=C.Thresholds(min_dp=0.0))
    assert off.rt["rate_steady"].sum() == res.rt["rate_steady"].sum()
    assert len(off.matched) == len(res.matched)
    np.testing.assert_allclose(off.cal["K_single"], res.cal["K_single"], rtol=1e-12)
    np.testing.assert_allclose(off.mape_overall["MAPE_all"], res.mape_overall["MAPE_all"], rtol=1e-12)


def test_dropping_the_dp_floor_admits_a_frozen_gauge_on_meleiha(mel):
    """On Meleiha the floor is rejecting M-80 ST rows pegged at dP = 199.4 psi. Admitting them
    drags in another well test and nearly doubles the error, which is why 300 stays the default."""
    off = run_pipeline("Meleiha", rules=C.Thresholds(min_dp=0.0))
    extra = off.rt["rate_steady"].sum() - mel.rt["rate_steady"].sum()
    assert extra > 2000
    admitted = off.rt[off.rt["rate_steady"] & (off.rt["dP"] <= C.MIN_DP)]
    assert admitted["WELL_NAME"].nunique() == 1
    assert admitted["dP"].nunique() == 1, "the admitted rows are one repeated value, not a signal"
    base_mape = mel.mape_overall.set_index("method")["MAPE_all"]
    off_mape = off.mape_overall.set_index("method")["MAPE_all"]
    assert off_mape["M1_LOO"] > base_mape["M1_LOO"] * 1.5
    assert len(off.matched) > len(mel.matched)


@pytest.mark.parametrize("rules", [
    C.Thresholds(min_dp=0.0),
    C.Thresholds(transient_gate=False),
    C.Thresholds(freq_gate=False, whp_over_pdp=False),
    C.Thresholds(pip_lo=0.0, pip_hi=20000.0),
])
def test_pipeline_runs_end_to_end_under_loosened_rules(rules):
    r = run_pipeline("GC31", rules=rules)
    assert len(r.cal) and len(r.matched) and len(r.validation)
    assert r.meta["rules"] == rules
    assert r.rt["rate_steady"].sum() > 0
