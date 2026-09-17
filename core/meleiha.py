"""Meleiha field (Egypt, 5 ESP wells, 2018-2021): loaders for the cleaned dataset.

The physics and every downstream step are identical to GC31; only the loaders and two quality
rules differ. See `data/meleiha/` and the source `README_new_wells_clean.md`.

What is different from GC31, and how it is handled here:

- **Two voltages and two currents.** `VOLTAGE_VSD_OUT` and `VOLTAGE_VSD_IN` are both on the
  low-voltage (drive) side of the step-up transformer, while `AMPERAGE_MOTOR` is motor-side.
  The app maps VOLTAGE := VOLTAGE_VSD_OUT and AMPERAGE := AMPERAGE_MOTOR and does **not**
  convert to motor voltage: the constant-K method absorbs the transformer ratio exactly as it
  does on GC31's SA-0162, as long as the ratio is constant. Where the ratio changed (a workover),
  the well is split into regimes and K is calibrated per regime.
- **Power factor was never logged** (100 % null); the previous analyst assumed 0.8.
- **The file carries its own quality flags.** `PUMP_RUNNING` and `GAUGE_FROZEN` are OR-ed and
  AND-ed into the app's own rules rather than replacing them.
- **Temperatures are already degF**, so the degC detection rule is switched off for this dataset:
  its only hits are zero-valued sensor dropouts, not Celsius readings.
- **No bubble point** exists for these wells, so the free-gas indicator is unavailable.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C

WELLS = ["M-145", "M-54", "M-80 ST", "SWM A-2-2", "SWM A-2x ST1"]

DATA_DIR = C.DATA_DIR / "meleiha"
RT_FILE = DATA_DIR / "realtime_clean_ALL.csv.gz"
TESTS_FILE = DATA_DIR / "well_tests_clean.csv"
STATIC_FILE = DATA_DIR / "well_static_clean.csv"
QUALITY_FILE = DATA_DIR / "realtime_quality_summary.csv"
ANALYST_FILE = DATA_DIR / "analyst_camilleri_series_ALL.csv"

# Bo is not available per test for Meleiha (the well tests carry water cut but no formation
# volume factor), so the B_liq water-cut correction uses this constant with a visible caveat.
ASSUMED_BO = 1.05
ASSUMED_BO_NOTE = (f"Bo is not in the Meleiha data; B_liq uses an assumed constant Bo = {ASSUMED_BO} "
                   "rb/stb, so only the water-cut part of the correction is real.")

BASIS_NOTE = ("Meleiha voltages are drive-side; K includes the transformer ratio. "
              "Power factor was never logged (analyst assumed 0.8).")

# native columns kept alongside the canonical ones
EXTRA_COLS = ["VOLTAGE_VSD_OUT", "VOLTAGE_VSD_IN", "VOLTAGE_SUPPLY", "AMPERAGE_MOTOR",
              "AMPERAGE_VSD", "INTAKE_TEMP_F", "MOTOR_TEMP_F", "WHT_F", "VIBRATION",
              "MOTOR_STATUS", "SOURCE_FILE", "XFMR_RATIO_LOGGED", "VSD_BASE_V", "VSD_BASE_HZ",
              "N_STARTS", "PUMP_RUNNING", "GAUGE_FROZEN", "DP_VALID", "ELEC_VALID",
              "USABLE_FOR_POWER_METHOD"]

VARIES_RE = re.compile(r"^VARIES\((\d+)\)")


# --------------------------------------------------------------------------- static / PVT

def parse_varies(value) -> tuple[float | str | None, int, str]:
    """(first value, number of distinct values, raw text) for a `VARIES(n): [a b c]` cell.

    The cleaned static file records a column that changed over the well's life as
    `VARIES(2): [120 252]`. A plain cell is one value.
    """
    raw = "" if value is None or (isinstance(value, float) and np.isnan(value)) else str(value)
    if not raw:
        return None, 0, ""
    m = VARIES_RE.match(raw)
    if not m:
        try:
            return float(raw), 1, raw
        except ValueError:
            return raw, 1, raw
    n = int(m.group(1))
    inside = raw[raw.find("[") + 1: raw.rfind("]")]
    parts = [p for p in re.split(r"[\s,]+", inside.strip()) if p]
    try:
        first = float(parts[0]) if parts else None
    except ValueError:
        first = parts[0] if parts else None
    return first, n, raw


def load_static(path: Path | str = STATIC_FILE, wells: list[str] | None = None) -> pd.DataFrame:
    """Per-well static data: pump, stages, depth and PVT, with the history-log (`_hist`) and
    workbook (`_wb`) variants kept side by side and a `disagrees` flag where they differ."""
    wells = WELLS if wells is None else wells
    t = pd.read_csv(path)
    t = t[t["WELL_NAME"].isin(wells)].copy()
    rows = []
    for _, r in t.iterrows():
        stages_wb, n_stages, stages_wb_raw = parse_varies(r.get("STAGES_wb"))
        ratio_wb, n_ratio, ratio_raw = parse_varies(r.get("XFMR_RATIO_wb"))
        depth_wb, _, depth_wb_raw = parse_varies(r.get("PUMP_DEPTH_FT_wb"))
        stages_hist = pd.to_numeric(r.get("STAGES_hist"), errors="coerce")
        depth_hist = pd.to_numeric(r.get("PUMP_DEPTH_FT_hist"), errors="coerce")
        rows.append(dict(
            WELL_NAME=r["WELL_NAME"],
            PUMP_MODEL=r.get("PUMP_MODEL_hist", ""),
            SERIES=pd.to_numeric(r.get("SERIES"), errors="coerce"),
            STAGES_hist=stages_hist, STAGES_wb=stages_wb, STAGES_wb_raw=stages_wb_raw,
            PUMP_DEPTH_FT_hist=depth_hist, PUMP_DEPTH_FT_wb=depth_wb, PUMP_DEPTH_FT_wb_raw=depth_wb_raw,
            XFMR_RATIO_wb=ratio_wb, XFMR_RATIO_wb_raw=ratio_raw,
            RS_SCF_STB=pd.to_numeric(r.get("RS_SCF_STB"), errors="coerce"),
            OIL_VISC_CP=pd.to_numeric(r.get("OIL_VISC_CP"), errors="coerce"),
            RES_TEMP_F=pd.to_numeric(r.get("RES_TEMP_F"), errors="coerce"),
            OIL_SG=parse_varies(r.get("OIL_SG"))[0],
            PF_assumed=parse_varies(r.get("PF_assumed_wb"))[0],
            n_regimes_declared=max(n_stages, n_ratio, 1),
        ))
    s = pd.DataFrame(rows)
    # the two sources disagree for three wells; both are shown rather than silently picking one
    s["stages_disagree"] = (s["STAGES_hist"].notna() & s["STAGES_wb"].notna()
                            & (s["STAGES_hist"] != s["STAGES_wb"]))
    s["depth_disagree"] = (s["PUMP_DEPTH_FT_hist"].notna() & s["PUMP_DEPTH_FT_wb"].notna()
                           & (s["PUMP_DEPTH_FT_hist"] != s["PUMP_DEPTH_FT_wb"]))
    # >1 regime when the workbook records the transformer ratio or the stage count changing
    s["multi_regime"] = s["n_regimes_declared"] > 1
    return s.sort_values("WELL_NAME").reset_index(drop=True)


# --------------------------------------------------------------------------- real time

def load_rt(path: Path | str = RT_FILE, wells: list[str] | None = None) -> pd.DataFrame:
    """Real-time frame mapped onto the app's canonical column names.

    VOLTAGE := VOLTAGE_VSD_OUT (drive side), AMPERAGE := AMPERAGE_MOTOR (motor side),
    MT := MOTOR_TEMP_F, INTAKE_TEMP := INTAKE_TEMP_F, WHT := WHT_F. The originals are kept.
    """
    wells = WELLS if wells is None else wells
    d = pd.read_csv(path, low_memory=False)
    d = d[d["WELL_NAME"].isin(wells)].copy()
    d["TIME_STAMP"] = pd.to_datetime(d["TIME_STAMP"])          # naive local time, UTC+2
    out = pd.DataFrame({
        "WELL_NAME": d["WELL_NAME"].astype(str).values,
        "TIME_STAMP": d["TIME_STAMP"].values,
        "GC_NAME": "Meleiha",
        "PRODUCTION_METHOD": "ESP",
        "WHP": pd.to_numeric(d["WHP"], errors="coerce").values,
        "WHT": pd.to_numeric(d["WHT_F"], errors="coerce").values,
        "FLP": np.nan,
        "PIP": pd.to_numeric(d["PIP"], errors="coerce").values,
        "PDP": pd.to_numeric(d["PDP"], errors="coerce").values,
        "INTAKE_TEMP": pd.to_numeric(d["INTAKE_TEMP_F"], errors="coerce").values,
        "MT": pd.to_numeric(d["MOTOR_TEMP_F"], errors="coerce").values,
        "FREQUENCY": pd.to_numeric(d["FREQUENCY"], errors="coerce").values,
        "VOLTAGE": pd.to_numeric(d["VOLTAGE_VSD_OUT"], errors="coerce").values,
        "AMPERAGE": pd.to_numeric(d["AMPERAGE_MOTOR"], errors="coerce").values,
    })
    for c in EXTRA_COLS:
        if c in d.columns:
            out[c] = d[c].values
    for c in ["PUMP_RUNNING", "GAUGE_FROZEN", "DP_VALID", "ELEC_VALID", "USABLE_FOR_POWER_METHOD"]:
        out[c] = out[c].astype(bool) if c in out.columns else False
    return out.sort_values(["WELL_NAME", "TIME_STAMP"], kind="mergesort").reset_index(drop=True)


def apply_quality(d: pd.DataFrame) -> pd.DataFrame:
    """Fold the file's own flags into the app's quality rules (spec section 2).

        pump_off := pump_off_rule OR NOT PUMP_RUNNING
        usable   := usable_rule AND NOT GAUGE_FROZEN

    `usable` and `steady` are recomputed afterwards because `pump_off` feeds them. The
    temperature-unit rule is switched off: these temperatures are already degF.
    """
    from .quality import EXCLUSION_FLAGS

    d = d.copy()
    d["gauge_frozen"] = d["GAUGE_FROZEN"].astype(bool) if "GAUGE_FROZEN" in d.columns else False
    if "PUMP_RUNNING" in d.columns:
        d["pump_off"] = d["pump_off"] | ~d["PUMP_RUNNING"].astype(bool)
    # already degF: the rule's only hits here are zero-valued sensor dropouts
    d["temp_unit_c"] = False
    d["MT_F"] = d["MT"]
    d["INTAKE_TEMP_F"] = d["INTAKE_TEMP"]
    d["usable"] = ~d[EXCLUSION_FLAGS].any(axis=1) & ~d["gauge_frozen"]
    d["steady"] = d["usable"] & ~d["transient"]
    return d


# --------------------------------------------------------------------------- regimes

def detect_regimes(d: pd.DataFrame, static: pd.DataFrame) -> pd.Series:
    """1-based regime index per row.

    A well whose workbook records the transformer ratio or the stage count changing runs in more
    than one electrical regime, and K is calibrated per regime. The boundary is the largest step
    change in the daily median VOLTAGE / FREQUENCY ratio, which is what a change of transformer
    tap or VSD base moves. Wells with a single declared value are all regime 1.
    """
    regime = pd.Series(1, index=d.index, dtype=int)
    multi = set(static.loc[static["multi_regime"], "WELL_NAME"]) if len(static) else set()
    for w in multi:
        m = d["WELL_NAME"] == w
        g = d.loc[m & d["PUMP_RUNNING"].astype(bool)] if "PUMP_RUNNING" in d.columns else d.loc[m]
        g = g[(g["FREQUENCY"] > 0) & g["VOLTAGE"].notna()]
        if g.empty:
            continue
        ratio = (g["VOLTAGE"] / g["FREQUENCY"]).groupby(g["TIME_STAMP"].dt.floor("D")).median().dropna()
        if len(ratio) < 10:
            continue
        step = ratio.diff().abs()
        step.iloc[0] = np.nan
        boundary = step.idxmax()
        regime.loc[m & (d["TIME_STAMP"] >= boundary)] = 2
    return regime


def regime_bounds(d: pd.DataFrame) -> pd.DataFrame:
    """(WELL_NAME, regime, start, end, n_rows) for every regime present."""
    g = d.groupby(["WELL_NAME", "regime"])
    out = g.agg(start=("TIME_STAMP", "min"), end=("TIME_STAMP", "max"), n_rows=("TIME_STAMP", "size"))
    return out.reset_index()


# --------------------------------------------------------------------------- well tests

def load_tests(path: Path | str = TESTS_FILE, wells: list[str] | None = None) -> pd.DataFrame:
    """Well tests mapped onto the app's canonical names.

    The tests carry a date but no time of day, so the mapper uses the +/-24 h window directly
    (see `Dataset.map_windows`) rather than trying +/-12 h first.
    """
    wells = WELLS if wells is None else wells
    t = pd.read_csv(path)
    t = t[t["WELL_NAME"].isin(wells)].copy()
    num = lambda c: pd.to_numeric(t[c], errors="coerce").values if c in t.columns else np.nan  # noqa: E731
    out = pd.DataFrame({
        "WELL_NAME": t["WELL_NAME"].astype(str).values,
        "TEST_TS": pd.to_datetime(t["DATE"]).values,           # date only, midnight local
        "Q_LIQ": num("Q_LIQ_BPD"), "Q_OIL": num("Q_OIL_BPD"),
        "WC": num("WC_PCT"), "GOR": num("GOR"),
        "T_FREQ": num("FREQ_HZ"), "T_WHP": num("SEP_P_PSI"),
        "T_PIP": num("PIP_PSI"), "T_PDP": num("PDP_PSI"),
        "T_CURRENT": num("CURRENT_A"), "T_TI_F": num("TI_F"), "T_TM_F": num("TM_F"),
        "T_WHT_F": num("WHT_F"),
        "STAGES": np.nan, "PUMP_DEPTH": np.nan, "FLUID_PPG": np.nan,
        "PUMP": "", "VALID": "",
        "IN_SCADA_WINDOW": t["IN_SCADA_WINDOW"].astype(bool).values if "IN_SCADA_WINDOW" in t else True,
        "Remarks": t["Remarks"].astype(str).values if "Remarks" in t else "",
    })
    out["SG"] = np.nan
    # water cut is measured per test; Bo is not, so B_liq uses an assumed constant Bo
    out["WC_FRAC"] = out["WC"] / 100.0
    out["BO"] = ASSUMED_BO
    out["B_LIQ"] = out["WC_FRAC"] * C.BW + (1 - out["WC_FRAC"]) * ASSUMED_BO
    out["B_LIQ_FILE"] = np.nan
    out["RS_TEST"] = np.nan
    out = out.sort_values(["WELL_NAME", "TEST_TS"], kind="mergesort").reset_index(drop=True)
    out["TEST_ID"] = out["WELL_NAME"] + " @ " + out["TEST_TS"].dt.strftime("%Y-%m-%d")
    return out


def test_pvt(tests: pd.DataFrame) -> pd.DataFrame:
    """Per-test PVT in the shape `core.pvt` expects."""
    return tests[["WELL_NAME", "TEST_TS", "WC_FRAC", "BO", "B_LIQ", "B_LIQ_FILE", "RS_TEST"]].copy()


def load_runs(static: pd.DataFrame | None = None) -> pd.DataFrame:
    """Pump metadata in the shape the well header expects. Meleiha has no installation dates,
    so there is no run boundary to draw; stage counts come from the history log."""
    s = load_static() if static is None else static
    return pd.DataFrame(dict(
        WELL_NAME=s["WELL_NAME"],
        install_date=pd.NaT,
        PUMP_MANUFACTURER="",
        CANONICAL_MODEL=s["PUMP_MODEL"].astype(str).str.split("(").str[0].str.strip(),
        NUMBER_OF_STAGES=s["STAGES_hist"],
        INSTALL_TOP_DEPTH_FT=s["PUMP_DEPTH_FT_hist"],
        n_tests_current=0, n_tests_previous=0,
    ))


def load_analyst(path: Path | str = ANALYST_FILE) -> pd.DataFrame:
    """The previous analyst's workbook series, for optional overlay only.

    Their calibration factor is recomputed at every row against the allocated rate, so the
    'calibrated' curve is pinned to the allocation and cannot be validated against it. It is
    never used for calibration or MAPE here.
    """
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=["WELL_NAME", "TIME_STAMP", "Q_ANALYST_CALIBRATED", "Q_LIQ_ALLOCATED"])
    a = pd.read_csv(p)
    a["TIME_STAMP"] = pd.to_datetime(a["TIME_STAMP"])
    keep = ["WELL_NAME", "TIME_STAMP", "Q_POWER_EQ_UNCALIBRATED", "ANALYST_CALIB_FACTOR",
            "Q_ANALYST_CALIBRATED", "Q_LIQ_ALLOCATED"]
    return a[[c for c in keep if c in a.columns]].sort_values(["WELL_NAME", "TIME_STAMP"]).reset_index(drop=True)


def load_quality_summary(path: Path | str = QUALITY_FILE) -> pd.DataFrame:
    p = Path(path)
    return pd.read_csv(p) if p.exists() else pd.DataFrame()
