"""Rule-based event detection on the daily series (spec section 7). No diagnostic matrix.

Each event: (WELL_NAME, start, end, type, severity, explanation, evidence, <numbers>).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

EVENT_TYPES = {
    "SCADA_GAP": "info",
    "PUMP_OFF": "warning",
    "VOLTAGE_BASIS_CHANGE": "warning",
    "TEMP_UNIT_SWITCH": "info",
    "RATE_STEP": "warning",
    "PHI_DRIFT": "critical",
    "BACKPRESSURE": "warning",
    "LOW_PIP_TREND": "warning",
    "SUSPECT_TEST": "critical",
}
EVENT_COLUMNS = ["WELL_NAME", "start", "end", "type", "severity", "duration_d",
                 "explanation", "evidence", "value_before", "value_after", "change_pct", "signal"]


def _runs(mask: pd.Series) -> list[tuple[int, int]]:
    """(start_pos, end_pos) inclusive positions of consecutive True runs."""
    m = mask.fillna(False).to_numpy(dtype=bool)
    if not m.any():
        return []
    edges = np.diff(np.concatenate(([0], m.astype(int), [0])))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1
    return list(zip(starts, ends))


def _event(well, start, end, etype, explanation, evidence: dict, signal=None,
           before=np.nan, after=np.nan, change=np.nan) -> dict:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    return dict(WELL_NAME=well, start=start, end=end, type=etype, severity=EVENT_TYPES[etype],
                duration_d=round((end - start).total_seconds() / 86400.0, 2),
                explanation=explanation,
                evidence="; ".join(f"{k}={v}" for k, v in evidence.items()),
                value_before=before, value_after=after, change_pct=change, signal=signal)


# --------------------------------------------------------------------------- row-level rules

def scada_gaps(d: pd.DataFrame, gap_days: float = C.GAP_DAYS) -> list[dict]:
    ev = []
    for w, g in d.groupby("WELL_NAME", sort=False):
        ts = g["TIME_STAMP"].sort_values().reset_index(drop=True)
        gap = ts.diff()
        for i in np.flatnonzero((gap > pd.Timedelta(days=gap_days)).to_numpy()):
            a, b = ts.iloc[i - 1], ts.iloc[i]
            days = (b - a).total_seconds() / 86400.0
            ev.append(_event(w, a, b, "SCADA_GAP",
                             f"No SCADA rows for {days:.1f} days.",
                             dict(last_row=a.strftime("%Y-%m-%d %H:%M"), next_row=b.strftime("%Y-%m-%d %H:%M"),
                                  gap_days=round(days, 1))))
    return ev


def pump_off_events(d: pd.DataFrame, min_hours: float = C.PUMP_OFF_HOURS) -> list[dict]:
    ev = []
    for w, g in d.groupby("WELL_NAME", sort=False):
        g = g.sort_values("TIME_STAMP").reset_index(drop=True)
        for a, b in _runs(g["pump_off"]):
            t0, t1 = g.loc[a, "TIME_STAMP"], g.loc[b, "TIME_STAMP"]
            # a run of 30-min rows a..b spans (b-a) intervals; add one nominal interval
            hours = (t1 - t0).total_seconds() / 3600.0 + 0.5
            if hours > min_hours:
                seg = g.loc[a:b]
                ev.append(_event(w, t0, t1, "PUMP_OFF",
                                 f"Pump off (V<100 V, I<5 A or Hz=0) for {hours:.1f} h.",
                                 dict(hours=round(hours, 1), rows=int(b - a + 1),
                                      median_V=round(float(seg["VOLTAGE"].median()), 0),
                                      median_I=round(float(seg["AMPERAGE"].median()), 1)),
                                 signal="VOLTAGE"))
    return ev


# --------------------------------------------------------------------------- daily rules

def voltage_basis_changes(daily: pd.DataFrame, pct: float = C.VOLT_CHANGE_PCT) -> list[dict]:
    ev = []
    for w, g in daily.groupby("WELL_NAME", sort=False):
        v = g.dropna(subset=["VOLTAGE"]).set_index("day")["VOLTAGE"]
        v = v[v > 0]
        chg = v.pct_change() * 100
        for day in chg.index[chg.abs() > pct]:
            prev_day = v.index[v.index.get_loc(day) - 1]
            b, a = v.loc[prev_day], v.loc[day]
            basis = f"{'LV' if b < C.LV_MV_SPLIT_V else 'MV'} -> {'LV' if a < C.LV_MV_SPLIT_V else 'MV'}"
            ev.append(_event(w, prev_day, day, "VOLTAGE_BASIS_CHANGE",
                             f"Daily median VOLTAGE moved {b:.0f} V -> {a:.0f} V ({chg.loc[day]:+.0f}%), "
                             f"basis {basis}. K is only valid on the calibrated basis.",
                             dict(V_before=round(b, 0), V_after=round(a, 0), change_pct=round(chg.loc[day], 1), basis=basis),
                             signal="VOLTAGE", before=b, after=a, change=chg.loc[day]))
    return ev


def temp_unit_switches(daily: pd.DataFrame) -> list[dict]:
    ev = []
    for w, g in daily.groupby("WELL_NAME", sort=False):
        gi = g.set_index("day")
        f = gi["temp_c_frac"].dropna()
        # daily majority state, smoothed over 3 data-days so a single mixed day does not flip it
        state = (f > 0.5).astype(int).rolling(3, center=True, min_periods=1).median().round().astype(int)
        flips = state.diff().fillna(0) != 0
        for day in state.index[flips]:
            prev_day = state.index[state.index.get_loc(day) - 1]
            b, a = ("degC" if state.loc[prev_day] else "degF"), ("degC" if state.loc[day] else "degF")
            raw_b, raw_a = gi.loc[prev_day, "MT_F"], gi.loc[day, "MT_F"]
            ev.append(_event(w, prev_day, day, "TEMP_UNIT_SWITCH",
                             f"Temperature tags switched from {b} to {a}; values are converted to degF for display.",
                             dict(before=b, after=a, frac_degC_before=round(float(f.loc[prev_day]), 2),
                                  frac_degC_after=round(float(f.loc[day]), 2)),
                             signal="MT_F", before=raw_b, after=raw_a))
    return ev


def rate_steps(daily: pd.DataFrame, q: str = "Q_interp", pct: float = C.RATE_STEP_PCT,
               min_days: int = C.RATE_STEP_MIN_DAYS, trail: int = C.RATE_STEP_TRAIL_DAYS) -> list[dict]:
    ev = []
    for w, g in daily.groupby("WELL_NAME", sort=False):
        s = g.dropna(subset=[q]).set_index("day")[q]
        if len(s) < trail + min_days:
            continue
        ref = s.shift(1).rolling(trail, min_periods=3).median()
        chg = (s / ref - 1) * 100
        for a, b in _runs(chg.abs() > pct):
            if b - a + 1 < min_days:
                continue
            seg = s.iloc[a:b + 1]
            before, after = float(ref.iloc[a]), float(seg.median())
            c = (after / before - 1) * 100
            ev.append(_event(w, s.index[a], s.index[b], "RATE_STEP",
                             f"Daily virtual rate {'dropped' if c < 0 else 'rose'} {abs(c):.0f}% "
                             f"({before:.0f} -> {after:.0f} BFPD) and held for {b - a + 1} days.",
                             dict(rate_before=round(before, 0), rate_after=round(after, 0), change_pct=round(c, 1),
                                  days=int(b - a + 1)),
                             signal=q, before=before, after=after, change=c))
    return ev


def phi_drifts(daily: pd.DataFrame, band=C.PHI_BAND, min_days: int = C.PHI_DRIFT_MIN_DAYS,
               roll: int = C.PHI_ROLL_DAYS) -> list[dict]:
    ev = []
    for w, g in daily.groupby("WELL_NAME", sort=False):
        s = g.dropna(subset=["PHI"]).set_index("day")["PHI"]
        if s.empty:
            continue
        r = s.rolling(roll, min_periods=3).median()
        for a, b in _runs((r < band[0]) | (r > band[1])):
            if b - a + 1 < min_days:
                continue
            seg = r.iloc[a:b + 1]
            phi = float(seg.median())
            ev.append(_event(w, s.index[a], s.index[b], "PHI_DRIFT",
                             f"7-day PHI stayed {'below' if phi < 1 else 'above'} the 0.95-1.05 band for "
                             f"{b - a + 1} data-days (median {phi:.2f}): recalibration recommended.",
                             dict(phi_median=round(phi, 3), phi_min=round(float(seg.min()), 3),
                                  phi_max=round(float(seg.max()), 3), days=int(b - a + 1)),
                             signal="PHI", before=1.0, after=phi, change=(phi - 1) * 100))
    return ev


def backpressure_events(daily: pd.DataFrame, factor: float = C.BACKPRESSURE_FACTOR,
                        trail: int = C.BACKPRESSURE_TRAIL_DAYS, min_days: int = C.BACKPRESSURE_MIN_DAYS) -> list[dict]:
    ev = []
    for w, g in daily.groupby("WELL_NAME", sort=False):
        s = g.dropna(subset=["WHP"]).set_index("day")["WHP"]
        if len(s) < 5:
            continue
        ref = s.shift(1).rolling(trail, min_periods=5).median()
        for a, b in _runs(s > factor * ref):
            if b - a + 1 < min_days:
                continue
            before, after = float(ref.iloc[a]), float(s.iloc[a:b + 1].median())
            ev.append(_event(w, s.index[a], s.index[b], "BACKPRESSURE",
                             f"WHP {after:.0f} psi is {after / before:.1f}x its 30-day median ({before:.0f} psi) "
                             f"for {b - a + 1} days: surface back-pressure / flowline restriction.",
                             dict(WHP_before=round(before, 0), WHP_during=round(after, 0),
                                  ratio=round(after / before, 2), days=int(b - a + 1)),
                             signal="WHP", before=before, after=after, change=(after / before - 1) * 100))
    return ev


def low_pip_trends(daily: pd.DataFrame, cal: pd.DataFrame, pct: float = C.LOW_PIP_PCT,
                   roll: int = C.LOW_PIP_ROLL_DAYS) -> list[dict]:
    ev = []
    base = cal.set_index("WELL_NAME")["PIP_base"]
    for w, g in daily.groupby("WELL_NAME", sort=False):
        if w not in base.index or not np.isfinite(base[w]):
            continue
        s = g.dropna(subset=["PIP"]).set_index("day")["PIP"]
        r = s.rolling(roll, min_periods=10).median()
        chg = (r / base[w] - 1) * 100
        for a, b in _runs(chg < -pct):
            seg = r.iloc[a:b + 1]
            ev.append(_event(w, s.index[a], s.index[b], "LOW_PIP_TREND",
                             f"30-day median PIP {seg.median():.0f} psi is {abs(chg.iloc[a:b + 1].median()):.0f}% below "
                             f"the first-calibration baseline ({base[w]:.0f} psi): deeper drawdown / inflow decline.",
                             dict(PIP_base=round(float(base[w]), 0), PIP_30d=round(float(seg.median()), 0),
                                  min_PIP_30d=round(float(seg.min()), 0), days=int(b - a + 1)),
                             signal="PIP", before=float(base[w]), after=float(seg.median()),
                             change=float(chg.iloc[a:b + 1].median())))
    return ev


def suspect_test_events(mm: pd.DataFrame) -> list[dict]:
    ev = []
    for _, r in mm[mm["suspect"]].iterrows():
        ev.append(_event(r["WELL_NAME"], r["TEST_TS"], r["TEST_TS"], "SUSPECT_TEST",
                         f"Well test {r['Q_LIQ']:.0f} BFPD gives K={r['K']:.1f}, robust z={r['robust_z']:.1f} "
                         f"vs the well's other tests: excluded from calibration.",
                         dict(Q_test=round(float(r["Q_LIQ"]), 0), K=round(float(r["K"]), 2),
                              robust_z=round(float(r["robust_z"]), 2)),
                         signal="Q_interp", after=float(r["K"]), change=float(r["robust_z"])))
    return ev


def detect_events(d: pd.DataFrame, daily: pd.DataFrame, mm: pd.DataFrame, cal: pd.DataFrame,
                  q: str = "Q_interp") -> pd.DataFrame:
    ev = (scada_gaps(d) + pump_off_events(d) + voltage_basis_changes(daily) + temp_unit_switches(daily)
          + rate_steps(daily, q) + phi_drifts(daily) + backpressure_events(daily)
          + low_pip_trends(daily, cal) + suspect_test_events(mm))
    if not ev:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    e = pd.DataFrame(ev)[EVENT_COLUMNS].sort_values(["WELL_NAME", "start", "type"]).reset_index(drop=True)
    e.insert(0, "event_id", range(1, len(e) + 1))
    return e


def events_in_window(events: pd.DataFrame, wells, start, end) -> pd.DataFrame:
    """Events overlapping [start, end] for the given wells."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if end.normalize() == end:
        end = end + pd.Timedelta(days=1)
    m = events["WELL_NAME"].isin(list(wells)) & (events["end"] >= start) & (events["start"] < end)
    return events[m]
