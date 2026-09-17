"""Load the two read-only input files and normalise them (spec section 1)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C

RT_NUMERIC = ["WHP", "WHT", "FLP", "PIP", "PDP", "INTAKE_TEMP", "MT", "FREQUENCY", "VOLTAGE", "AMPERAGE"]

TEST_COLUMNS = {
    "P26: Liquid Rate / BFPD": "Q_LIQ",
    "P27: Oil Rtae /BOPD": "Q_OIL",
    "P29: W.C %": "WC",
    "P30: GOR / SCF/STB": "GOR",
    "P34: Mtr. Freq. /hz": "T_FREQ",
    "P35: WHP Psi": "T_WHP",
    "P39: P.Intake Pressure /psi": "T_PIP",
    "P40: P. Discharge Pressure /psi": "T_PDP",
    "P13: nr. Of Stages": "STAGES",
    "P64: pump intake /TVD": "PUMP_DEPTH",
    "P55: Fluid desity ppg": "FLUID_PPG",
}
WATER_PPG = 8.33          # ppg of fresh water, for the specific-gravity column
TEST_TEXT_COLUMNS = {
    "P11: Pump type": "PUMP",
    "P81: Well test validiation": "VALID",
}


def load_rt(path: Path | str = C.RT_FILE, wells: list[str] | None = None) -> pd.DataFrame:
    """Real-time SCADA frame for the selected wells, sorted by well and time.

    Loads every well in WELLS_ALL by default, including excluded ones: they are displayed
    on the Data quality page and must never be silently missing.

    Every row of the source file for these wells is kept; nothing is dropped here.
    """
    wells = C.WELLS_ALL if wells is None else wells
    path = Path(path)
    if path.suffix == ".parquet":
        d = pd.read_parquet(path)
    else:
        d = pd.read_excel(path, sheet_name=C.RT_SHEET)
    d = d[d["WELL_NAME"].isin(wells)].copy()
    d["TIME_STAMP"] = pd.to_datetime(d["TIME_STAMP"])
    for c in RT_NUMERIC:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.sort_values(["WELL_NAME", "TIME_STAMP"], kind="mergesort").reset_index(drop=True)
    d["WELL_NAME"] = d["WELL_NAME"].astype(str)
    return d


def load_tests(path: Path | str = C.WT_FILE, wells: list[str] | None = None) -> pd.DataFrame:
    """Well-test frame for the selected wells with numeric coercion.

    Placeholders such as DATA_UNRECORDED / MISSING_HARDWARE_SPEC become NaN.
    """
    wells = C.WELLS_ALL if wells is None else wells
    t = pd.read_csv(path, low_memory=False)
    t = t[t["WELL_NAME"].isin(wells)].copy()
    out = pd.DataFrame({
        "WELL_NAME": t["WELL_NAME"].astype(str).values,
        "TEST_TS": pd.to_datetime(t["Test Timestamp"]).values,
    })
    for src, dst in TEST_COLUMNS.items():
        out[dst] = pd.to_numeric(t[src], errors="coerce").values if src in t.columns else np.nan
    for src, dst in TEST_TEXT_COLUMNS.items():
        out[dst] = t[src].astype(str).values if src in t.columns else ""
    out["SG"] = out["FLUID_PPG"] / WATER_PPG
    out = out.sort_values(["WELL_NAME", "TEST_TS"], kind="mergesort").reset_index(drop=True)
    out["TEST_ID"] = out["WELL_NAME"] + " @ " + out["TEST_TS"].dt.strftime("%Y-%m-%d %H:%M")
    return out


ESP_COLUMNS = ["PUMP_MANUFACTURER", "CANONICAL_MODEL", "NUMBER_OF_STAGES", "INSTALL_TOP_DEPTH_FT",
               "DAYS_FROM_INSTALLATION", "IS_ACTIVE_CURRENT_RUN"]


def load_esp_master(path: Path | str = C.ESP_MASTER_FILE, wells: list[str] | None = None) -> pd.DataFrame:
    """Per-test pump-run metadata for the selected wells (ESP_MASTER_DATASET.csv).

    DAYS_FROM_INSTALLATION < 0 (or IS_ACTIVE_CURRENT_RUN False) marks a test from a previous run.
    """
    wells = C.WELLS_ALL if wells is None else wells
    t = pd.read_csv(path, low_memory=False)
    t = t[t["WELL_NAME"].isin(wells)].copy()
    out = pd.DataFrame({"WELL_NAME": t["WELL_NAME"].astype(str).values,
                        "TEST_TS": pd.to_datetime(t["TEST_DATE_TIME"]).values})
    for c in ESP_COLUMNS:
        out[c] = t[c].values if c in t.columns else np.nan
    for c in ["NUMBER_OF_STAGES", "INSTALL_TOP_DEPTH_FT", "DAYS_FROM_INSTALLATION"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out["IS_ACTIVE_CURRENT_RUN"] = out["IS_ACTIVE_CURRENT_RUN"].astype(str).str.lower().isin(["true", "1", "yes"])
    out["run"] = np.where(out["IS_ACTIVE_CURRENT_RUN"] & (out["DAYS_FROM_INSTALLATION"] >= 0), "current", "previous")
    out["INSTALL_DATE"] = out["TEST_TS"] - pd.to_timedelta(out["DAYS_FROM_INSTALLATION"], unit="D")
    return out.sort_values(["WELL_NAME", "TEST_TS"], kind="mergesort").reset_index(drop=True)


def pump_runs(esp: pd.DataFrame) -> pd.DataFrame:
    """One row per well describing the CURRENT pump run: install date (run boundary), pump
    manufacturer/model/stages/depth and how many tests fall in the current vs previous run."""
    rows = []
    for w, g in esp.groupby("WELL_NAME"):
        cur = g[g["run"] == "current"]
        ref = cur if len(cur) else g
        rows.append(dict(
            WELL_NAME=w,
            install_date=ref["INSTALL_DATE"].median().floor("D") if ref["INSTALL_DATE"].notna().any() else pd.NaT,
            PUMP_MANUFACTURER=ref["PUMP_MANUFACTURER"].mode().iloc[0] if ref["PUMP_MANUFACTURER"].notna().any() else "",
            CANONICAL_MODEL=ref["CANONICAL_MODEL"].mode().iloc[0] if ref["CANONICAL_MODEL"].notna().any() else "",
            NUMBER_OF_STAGES=int(ref["NUMBER_OF_STAGES"].median()) if ref["NUMBER_OF_STAGES"].notna().any() else np.nan,
            INSTALL_TOP_DEPTH_FT=float(ref["INSTALL_TOP_DEPTH_FT"].median()) if ref["INSTALL_TOP_DEPTH_FT"].notna().any() else np.nan,
            n_tests_current=int(len(cur)), n_tests_previous=int(len(g) - len(cur)),
        ))
    return pd.DataFrame(rows)


def run_label(times: pd.Series, well: pd.Series, runs: pd.DataFrame) -> pd.Series:
    """'current' if the timestamp is on/after the well's current-run install date, else 'previous'."""
    inst = runs.set_index("WELL_NAME")["install_date"]
    boundary = well.map(inst)
    # a dataset without installation dates has a single, current run
    return pd.Series(np.where(boundary.isna() | (pd.to_datetime(times) >= boundary), "current", "previous"),
                     index=times.index)
