"""Meleiha dataset: loader, quality rules, regimes, matching and calibration.

Expected values come from the source `README_new_wells_clean.md` and the addendum spec. Where an
expectation and a mandated quality rule disagree, the rule wins and the test records why.
"""
import numpy as np
import pandas as pd
import pytest

from core import config as C
from core import datasets as DS
from core import meleiha as ML
from core.pipeline import run_pipeline

WELLS = ["M-145", "M-54", "M-80 ST", "SWM A-2-2", "SWM A-2x ST1"]

# Tests inside the SCADA window, per the source README. These count tests between the first and
# last SCADA timestamp, BEFORE any quality filtering, so they are an upper bound on the matches.
IN_WINDOW = {"SWM A-2-2": 26, "M-80 ST": 21, "M-54": 9, "SWM A-2x ST1": 9, "M-145": 8}
# What actually survives the mandated quality rules (frozen gauges, pressure rails, export gaps).
EXPECTED_MATCHED = {"SWM A-2-2": 14, "M-80 ST": 12, "M-54": 3, "SWM A-2x ST1": 9, "M-145": 8}

GAUGE_FROZEN_FROM = pd.Timestamp("2019-06-09")


# --------------------------------------------------------------------------- loader

def test_row_count_and_wells(mel):
    assert len(mel.rt) == 402_718
    assert sorted(mel.rt["WELL_NAME"].unique()) == sorted(WELLS)
    assert mel.meta["dataset"] == "Meleiha"
    assert mel.rt["TIME_STAMP"].notna().all()
    for _, g in mel.rt.groupby("WELL_NAME"):
        assert g["TIME_STAMP"].is_monotonic_increasing


def test_canonical_column_mapping(mel):
    """VOLTAGE := VOLTAGE_VSD_OUT (drive side), AMPERAGE := AMPERAGE_MOTOR (motor side)."""
    d = mel.rt
    for canon, native in [("VOLTAGE", "VOLTAGE_VSD_OUT"), ("AMPERAGE", "AMPERAGE_MOTOR"),
                          ("MT", "MOTOR_TEMP_F"), ("INTAKE_TEMP", "INTAKE_TEMP_F"), ("WHT", "WHT_F")]:
        assert native in d.columns, f"the original {native} must be kept"
        a, b = d[canon], d[native]
        both = a.notna() & b.notna()
        assert np.allclose(a[both], b[both]), canon
    # WHT exists only on SWM A-2-2
    have_wht = d.groupby("WELL_NAME")["WHT"].apply(lambda s: s.notna().any())
    assert have_wht["SWM A-2-2"] and not have_wht.drop("SWM A-2-2").any()


def test_no_rows_dropped(mel):
    d = mel.rt
    assert d["usable"].sum() < len(d)
    assert (d["steady"] <= d["usable"]).all()
    assert (d["rate_steady"] <= d["rate_ok"]).all()


# --------------------------------------------------------------------------- quality rules

def test_file_flags_are_folded_in(mel):
    """pump_off OR NOT PUMP_RUNNING, and usable AND NOT GAUGE_FROZEN."""
    d = mel.rt
    assert (d.loc[~d["PUMP_RUNNING"].astype(bool), "pump_off"]).all()
    assert not d.loc[d["gauge_frozen"], "usable"].any()
    assert (d["gauge_frozen"] == d["GAUGE_FROZEN"].astype(bool)).all()


def test_temperature_unit_rule_never_fires(mel):
    """Meleiha temperatures are already degF; the degC detection must stay off."""
    assert not mel.rt["temp_unit_c"].any()
    assert np.allclose(mel.rt["MT_F"].dropna(), mel.rt["MT"].dropna())


def test_m54_gauge_failure_is_excluded(mel):
    """M-54's gauges peg at 1756/1755 psi from 09-Jun-2019 and those rows must carry no rate.

    The addendum expected the file's own GAUGE_FROZEN flag to catch this, and the source README
    says it does, but the flag fires on 0 % of the post-failure rows: the pegged reading jitters
    slightly, so the cleaner's rolling-day no-change test never trips. What does catch it is the
    app's own dP rule, because PDP 1755 sits below PIP 1756 and dP is about -1 psi. The outcome
    is what matters, so that is what this asserts, with the mechanism recorded.
    """
    d = mel.rt[(mel.rt["WELL_NAME"] == "M-54") & mel.rt["PUMP_RUNNING"].astype(bool)]
    after = d[d["TIME_STAMP"] >= GAUGE_FROZEN_FROM]
    assert len(after) > 1000
    assert after["rate_ok"].mean() * 100 < 1, "post-failure rows must not carry a rate"
    assert after["bad_dP"].mean() * 100 > 99, "the dP rule is what rejects them"
    assert (after["PIP"].round(0) == 1756).mean() * 100 > 99      # the pegged value
    # the file's flag is kept and OR-ed in, it simply does not fire here
    assert mel.rt["gauge_frozen"].sum() > 0


def test_pressure_rail_needs_a_pdp_ceiling(mel):
    """M-80 ST pegs PDP at 6554 psi (a 16-bit rail).

    No PDP ceiling is applied by default - discharge pressure spans too wide a range between
    fields to bound sensibly, and SWM A-2-2 on this same field reaches 53,947 psi - so these rows
    do carry a rate until someone sets one. They are the case the dP floor cannot catch: PIP moves
    underneath the pegged value, so dP stays a plausible 4,700-5,800 psi.

    The Filter rules page exposes the ceiling for exactly this. Setting it removes the rail and
    the error against the well tests falls, which is the evidence for setting it on this field.
    """
    rail = mel.rt[(mel.rt["WELL_NAME"] == "M-80 ST") & (mel.rt["PDP"].round(0) == 6554)]
    assert len(rail) > 40_000
    assert rail["dP"].min() > C.MIN_DP, "the dP floor cannot see a pegged PDP"
    assert rail["rate_ok"].any(), "no ceiling by default, so the rail is not rejected"

    capped = run_pipeline("Meleiha", rules=C.Thresholds(pdp_max=6000.0))
    capped_rail = capped.rt[(capped.rt["WELL_NAME"] == "M-80 ST")
                            & (capped.rt["PDP"].round(0) == 6554)]
    assert not capped_rail["rate_ok"].any()
    base = mel.mape_overall.set_index("method")["MAPE_all"]
    with_cap = capped.mape_overall.set_index("method")["MAPE_all"]
    assert with_cap["M1_LOO"] < base["M1_LOO"]


# --------------------------------------------------------------------------- regimes

def test_m80_has_two_regimes(mel):
    r = mel.regimes[mel.regimes["WELL_NAME"] == "M-80 ST"]
    assert len(r) == 2, f"expected 2 regimes, got {len(r)}"
    assert sorted(r["regime"]) == [1, 2]
    boundary = pd.Timestamp(r.sort_values("regime")["start"].iloc[1])
    assert pd.Timestamp("2019-01-01") < boundary < pd.Timestamp("2019-12-31")
    # every other well runs in a single regime
    others = mel.regimes[mel.regimes["WELL_NAME"] != "M-80 ST"]
    assert (others.groupby("WELL_NAME").size() == 1).all()


def test_k_is_calibrated_per_regime(mel):
    cal = mel.cal
    m80 = cal[cal["WELL_NAME"] == "M-80 ST"].sort_values("regime")
    assert len(m80) == 2
    assert m80["K_single"].is_monotonic_increasing        # the ratio change raises K
    assert m80["K_single"].iloc[1] / m80["K_single"].iloc[0] > 1.3


def test_varies_parsing():
    first, n, raw = ML.parse_varies("VARIES(3): [0.16 0.11 0.12]")
    assert (first, n) == (0.16, 3) and raw.startswith("VARIES")
    assert ML.parse_varies("0.14") == (0.14, 1, "0.14")
    assert ML.parse_varies(np.nan) == (None, 0, "")
    s = ML.load_static()
    assert set(s["WELL_NAME"]) == set(WELLS)
    assert bool(s.set_index("WELL_NAME").loc["M-80 ST", "multi_regime"])
    assert not bool(s.set_index("WELL_NAME").loc["M-145", "multi_regime"])
    assert s["stages_disagree"].sum() >= 2                 # log and workbook differ on three wells


# --------------------------------------------------------------------------- mapping and K

@pytest.mark.parametrize("well,n_exp", list(EXPECTED_MATCHED.items()))
def test_matched_counts(mel, well, n_exp):
    got = int((mel.matched["WELL_NAME"] == well).sum())
    assert abs(got - n_exp) <= 3, f"{well}: {got} matched, expected ~{n_exp}"


def test_matched_counts_are_bounded_by_the_window(mel):
    """Matching can only lose tests relative to the source README's in-window count."""
    got = mel.matched.groupby("WELL_NAME").size()
    for w, cap in IN_WINDOW.items():
        assert got.get(w, 0) <= cap, w
    assert len(mel.matched) == int(got.sum()) >= 40


def test_m54_late_tests_fail_on_frozen_gauges(mel):
    """Only the Apr-Jun 2019 tests are usable; the later ones must fail, not match."""
    m = mel.mapped[mel.mapped["WELL_NAME"] == "M-54"]
    matched = m[m["MATCH"] == "MATCHED"]
    assert (matched["TEST_TS"] < GAUGE_FROZEN_FROM).all()
    late = m[(m["TEST_TS"] >= GAUGE_FROZEN_FROM) & m["IN_SCADA_WINDOW"]]
    assert len(late) >= 2 and (late["MATCH"] != "MATCHED").all()


def test_k_positive_and_finite(mel):
    cal = mel.cal
    assert len(cal) >= 5
    assert np.isfinite(cal["K_single"]).all() and (cal["K_single"] > 0).all()
    assert np.isfinite(cal["K_dh_single"]).all() and (cal["K_dh_single"] > 0).all()
    assert cal["n_tests"].ge(2).all()


def test_mape_table_is_produced(mel):
    """No expected values yet: the table must exist, be finite and beat nothing by construction."""
    mo = mel.mape_overall.set_index("method")
    for m in ["M1_LOO", "M2_WALK", "BASE_LAST_TEST"]:
        assert np.isfinite(mo.loc[m, "MAPE_all"]) and mo.loc[m, "MAPE_all"] > 0, m
        assert np.isfinite(mo.loc[m, "MdAPE_all"])
        assert mo.loc[m, "n_all"] > 0
    assert set(mel.mape["scope"]) == {"ALL"} | set(mel.wells_analysed)


def test_water_cut_correction_uses_an_assumed_bo(mel):
    """Bo is not measured per test here, so B_liq uses a constant with a stated caveat."""
    t = mel.test_pvt
    assert t["BO"].dropna().eq(ML.ASSUMED_BO).all()
    assert t["WC_FRAC"].dropna().between(0, 1).all()
    assert DS.MELEIHA.wc_correction_note.strip()
    d = mel.rt[mel.rt["rate_steady"]]
    assert d["B_LIQ"].dropna().between(1.0, 1.06).all()


def test_no_bubble_point(mel):
    assert not DS.MELEIHA.has_bubble_point
    assert len(mel.gas) == 0
    assert mel.rt["Pb"].isna().all()


def test_auto_exclusion_rule_applies(mel):
    """No well is excluded by configuration, but the < 2 matched tests rule still runs."""
    assert DS.MELEIHA.excluded_wells == {}
    for _, r in mel.excluded.iterrows():
        assert r["excluded_by"] == "insufficient data"
        assert "matched well test" in r["reason"]
    assert set(mel.wells_analysed) | set(mel.excluded.get("WELL_NAME", [])) == set(WELLS)


def test_analyst_series_is_reference_only(mel):
    """Loaded for overlay, never used for calibration or MAPE."""
    a = mel.analyst
    assert len(a) > 500 and "Q_ANALYST_CALIBRATED" in a.columns
    assert not any("ANALYST" in c.upper() for c in mel.matched.columns)
    assert not any("ANALYST" in c.upper() for c in mel.validation.columns)


# --------------------------------------------------------------------------- registry

def test_registry():
    assert set(DS.DATASETS) == {"GC31", "Meleiha"}
    assert DS.DEFAULT_DATASET == "GC31"
    assert DS.get().key == "GC31"
    assert DS.get("Meleiha").map_windows == (24,)          # tests carry a date only
    assert DS.GC31.map_windows == (12, 24)
    assert "Meleiha" in DS.available()


# --------------------------------------------------------------------------- dataset switching

def test_sidebar_dataset_switch_round_trip():
    """Switching field reloads without error and leaves no selection from the other one."""
    from pathlib import Path

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=300)
    at.switch_page("app_pages/overview.py")
    at.run()
    gc = at.session_state["filters"]["wells"]
    assert gc and all(w.startswith("SA-") for w in gc)

    at.selectbox("dataset").set_value("Meleiha").run()
    assert not at.exception
    mel_wells = at.session_state["filters"]["wells"]
    assert set(mel_wells) == set(WELLS)
    assert at.session_state["filters"]["dataset"] == "Meleiha"

    at.selectbox("dataset").set_value("GC31").run()
    assert not at.exception
    assert at.session_state["filters"]["wells"] == gc
    assert at.session_state["filters"]["dataset"] == "GC31"


def test_gc31_is_untouched_by_the_second_dataset(res, mel):
    """The two datasets share the pipeline but never share a number."""
    assert res.meta["dataset"] == "GC31" and mel.meta["dataset"] == "Meleiha"
    assert not set(res.rt["WELL_NAME"]) & set(mel.rt["WELL_NAME"])
    assert res.meta["n_matched"] == 13 and mel.meta["n_matched"] >= 40
    assert (res.rt["regime"] == 1).all()                  # GC31 has a single regime everywhere
