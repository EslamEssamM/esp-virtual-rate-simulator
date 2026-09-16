"""Unit tests on the pure functions with small synthetic frames (no data files needed)."""
import numpy as np
import pandas as pd
import pytest

from core import quality, calibration, diagnosis, mapping, validation


def _rt(n=20, well="W1", start="2025-01-01", V=2000.0, I=30.0, PIP=1000.0, PDP=3000.0, freq=50.0):
    ts = pd.date_range(start, periods=n, freq="30min")
    return pd.DataFrame(dict(WELL_NAME=well, TIME_STAMP=ts, WHP=200.0, WHT=np.nan, FLP=150.0,
                             PIP=PIP, PDP=PDP, INTAKE_TEMP=170.0, MT=230.0, FREQUENCY=freq,
                             VOLTAGE=V, AMPERAGE=I))


def test_flags_basic():
    d = _rt()
    d.loc[0, "VOLTAGE"] = 0.0                # pump off
    d.loc[1, "PIP"] = np.nan                 # missing pressure
    d.loc[2, "PDP"] = 1200.0                 # dP = 200 -> bad_dP
    d.loc[3, "FREQUENCY"] = 20.0             # bad freq
    d.loc[4, "FREQUENCY"] = np.nan           # missing freq is fine
    d.loc[5, "WHP"] = 5000.0                 # WHP > PDP
    d.loc[6, "MT"] = 100.0                   # degC
    f = quality.add_quality_flags(d)
    assert f.loc[0, "pump_off"] and not f.loc[0, "usable"]
    assert f.loc[1, "missing_press"] and f.loc[1, "bad_dP"] and not f.loc[1, "usable"]
    assert f.loc[2, "bad_dP"] and not f.loc[2, "usable"]
    assert f.loc[3, "bad_freq"] and not f.loc[3, "usable"]
    assert not f.loc[4, "bad_freq"] and f.loc[4, "usable"]
    assert f.loc[5, "bad_press_range"]
    assert f.loc[6, "temp_unit_c"] and f.loc[6, "MT_F"] == pytest.approx(212.0)
    assert len(f) == len(d)                  # nothing dropped
    assert f.loc[10, "X"] == pytest.approx(np.sqrt(3) * 2000 * 30 / 2000)
    assert f.loc[0, "V_BASIS"] == "LV" and (f.loc[1:, "V_BASIS"] == "MV").all()


def test_transient_flag():
    d = _rt(n=30)
    d.loc[15:17, "AMPERAGE"] = [30, 45, 30]  # spike
    f = quality.add_quality_flags(d)
    assert f["transient"].iloc[16:20].any()
    assert not f["transient"].iloc[:10].any()
    assert (f["steady"] == (f["usable"] & ~f["transient"])).all()


def test_freq_filled_ffill_then_monthly():
    d = _rt(n=200)
    d.loc[10:150, "FREQUENCY"] = np.nan
    f = quality.add_quality_flags(d)
    assert f.loc[20, "FREQ_FILLED"] == 50.0            # within 24 h -> ffill
    assert f.loc[100, "FREQ_FILLED"] == 50.0           # beyond 24 h -> monthly median (all 50)
    assert f["FREQ_FILLED"].notna().all()


def test_robust_z_and_suspect():
    k = pd.Series([10.0, 10.2, 9.9, 10.1, 30.0])
    z = calibration.robust_z(k)
    assert z.iloc[-1] > 3.5 and (z.iloc[:-1] < 3.5).all()
    m = pd.DataFrame(dict(WELL_NAME="W1", TEST_TS=pd.date_range("2025-01-01", periods=5, freq="30D"),
                          Q_LIQ=1000.0, RT_X=1000.0 / k, K=k, MATCH="MATCHED",
                          RT_VOLTAGE=2000.0, RT_dP=2000.0, RT_P_elec_kVA=100.0, RT_PIP=1000.0, V_BASIS="MV"))
    mm, cal = calibration.calibrate(m)
    assert mm["suspect"].tolist() == [False, False, False, False, True]
    assert cal.loc[0, "K_single"] == pytest.approx(10.05)
    assert cal.loc[0, "n_tests"] == 4 and cal.loc[0, "n_suspect"] == 1
    assert cal.loc[0, "V_lo"] == pytest.approx(1600) and cal.loc[0, "V_hi"] == pytest.approx(2400)


def test_k_interp_flat_outside():
    c = pd.DataFrame(dict(TEST_TS=pd.to_datetime(["2025-01-01", "2025-01-11"]), K=[10.0, 20.0], suspect=[False, False]))
    t = pd.Series(pd.to_datetime(["2024-12-01", "2025-01-06", "2025-02-01"]))
    k = calibration.k_interp(t, c)
    assert k.tolist() == pytest.approx([10.0, 15.0, 20.0])


def test_mapping_window_and_fallback():
    d = quality.add_quality_flags(_rt(n=48 * 3, start="2025-01-01"))   # 3 days of steady rows
    tests = pd.DataFrame(dict(WELL_NAME=["W1", "W1", "W2"],
                              TEST_TS=pd.to_datetime(["2025-01-02 12:00", "2025-01-10 12:00", "2025-01-02 12:00"]),
                              Q_LIQ=[1000.0, 900.0, 800.0], T_PIP=[990.0, np.nan, 1000.0]))
    m = mapping.map_tests(d, tests)
    assert m["MATCH"].tolist() == ["MATCHED", "NO_RT_DATA", "NO_RT_DATA"]
    assert m.loc[0, "window_h"] == 12 and m.loc[0, "n_steady"] == 49
    assert m.loc[0, "K"] == pytest.approx(1000.0 / (np.sqrt(3) * 2000 * 30 / 2000))
    assert m.loc[0, "PIP_diff_vs_test"] == pytest.approx(10.0)
    # too few steady rows in +/-12 h but enough in +/-24 h -> window widens
    d2 = d.copy()
    d2.loc[(d2.TIME_STAMP >= "2025-01-02 00:30") & (d2.TIME_STAMP <= "2025-01-02 23:30"), "steady"] = False
    m2 = mapping.map_tests(d2, tests.iloc[:1])
    assert m2.loc[0, "MATCH"] == "MATCHED" and m2.loc[0, "window_h"] == 24


def test_validation_methods():
    ts = pd.date_range("2025-01-01", periods=3, freq="60D")
    mm = pd.DataFrame(dict(WELL_NAME="W1", TEST_TS=ts, Q_LIQ=[1000.0, 1100.0, 1200.0],
                           RT_X=[100.0, 100.0, 100.0], K=[10.0, 11.0, 12.0], suspect=False))
    tests = mm[["WELL_NAME", "TEST_TS", "Q_LIQ"]].copy()
    v = validation.validate(mm, tests)
    assert np.isnan(v.loc[0, "Q_M2_WALK"]) and np.isnan(v.loc[0, "Q_BASE_LAST_TEST"])
    assert v.loc[1, "Q_M2_WALK"] == pytest.approx(1000.0)          # K of previous test x X
    assert v.loc[1, "Q_BASE_LAST_TEST"] == pytest.approx(1000.0)
    assert v.loc[1, "Q_M1_LOO"] == pytest.approx(11.0 * 100)        # median(10, 12) x 100
    assert v.loc[1, "APE_M1_LOO"] == pytest.approx(0.0)
    s = validation.mape_summary(v)
    assert set(s["method"]) == {"M1_LOO", "M2_WALK", "BASE_LAST_TEST"}


def test_runs_helper():
    m = pd.Series([False, True, True, False, True, False, False, True])
    assert diagnosis._runs(m) == [(1, 2), (4, 4), (7, 7)]
    assert diagnosis._runs(pd.Series([False, False])) == []


def test_scada_gap_and_pump_off_events():
    a = _rt(n=10, start="2025-01-01")
    b = _rt(n=30, start="2025-01-05")            # 4-day gap
    b.loc[5:25, "VOLTAGE"] = 0.0                 # 21 rows off = 10.5 h
    d = quality.add_quality_flags(pd.concat([a, b], ignore_index=True))
    ev = diagnosis.scada_gaps(d) + diagnosis.pump_off_events(d)
    types = [e["type"] for e in ev]
    assert types.count("SCADA_GAP") == 1 and types.count("PUMP_OFF") == 1
    gap = [e for e in ev if e["type"] == "SCADA_GAP"][0]
    assert gap["duration_d"] == pytest.approx(3.8, abs=0.05)


def test_daily_rules_on_synthetic_series():
    days = pd.date_range("2025-01-01", periods=60, freq="D")
    daily = pd.DataFrame(dict(day=days, WELL_NAME="W1", VOLTAGE=2000.0, temp_c_frac=0.0, Q_interp=1000.0,
                              PHI=1.0, WHP=200.0, PIP=1000.0, MT_F=230.0))
    daily.loc[30:, "VOLTAGE"] = 1200.0           # -40 % step
    daily.loc[30:, "temp_c_frac"] = 1.0
    daily.loc[20:, "Q_interp"] = 700.0           # -30 % step, holds
    daily.loc[40:, "PHI"] = 0.90
    daily.loc[50:53, "WHP"] = 400.0
    daily.loc[25:, "PIP"] = 800.0                # 30-day median falls below 0.85 x baseline
    cal = pd.DataFrame(dict(WELL_NAME=["W1"], run=["current"], run_start=[days[0]],
                            run_end=[days[-1] + pd.Timedelta(days=1)], PIP_base=[1000.0], base_test_ts=[days[0]]))
    ev = (diagnosis.voltage_basis_changes(daily) + diagnosis.temp_unit_switches(daily)
          + diagnosis.rate_steps(daily) + diagnosis.phi_drifts(daily)
          + diagnosis.backpressure_events(daily) + diagnosis.low_pip_trends(daily, cal))
    types = {e["type"] for e in ev}
    assert types == {"VOLTAGE_BASIS_CHANGE", "TEMP_UNIT_SWITCH", "RATE_STEP", "PHI_DRIFT", "BACKPRESSURE", "LOW_PIP_TREND"}
    daily2 = daily.copy(); daily2["VOLTAGE"] = 2000.0; daily2.loc[30:, "VOLTAGE"] = 1700.0   # -15 % -> step tier
    assert [e["type"] for e in diagnosis.voltage_basis_changes(daily2)] == ["VOLTAGE_STEP"]
    rs = [e for e in ev if e["type"] == "RATE_STEP"][0]
    assert rs["change_pct"] == pytest.approx(-30.0, abs=0.5)


def test_k_interp_mixed_time_units():
    c = pd.DataFrame(dict(TEST_TS=pd.to_datetime(["2025-01-01", "2025-01-11"]).astype("datetime64[us]"),
                          K=[10.0, 20.0], suspect=[False, False]))
    t = pd.Series(pd.to_datetime(["2025-01-06"]).astype("datetime64[ns]"))
    assert calibration.k_interp(t, c).tolist() == pytest.approx([15.0])
