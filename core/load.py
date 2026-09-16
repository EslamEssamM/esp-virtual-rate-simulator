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
TEST_TEXT_COLUMNS = {
    "P11: Pump type": "PUMP",
    "P81: Well test validiation": "VALID",
}


def load_rt(path: Path | str = C.RT_FILE, wells: list[str] = C.WELLS) -> pd.DataFrame:
    """Real-time SCADA frame for the selected wells, sorted by well and time.

    Every row of the source file for these wells is kept; nothing is dropped here.
    """
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


def load_tests(path: Path | str = C.WT_FILE, wells: list[str] = C.WELLS) -> pd.DataFrame:
    """Well-test frame for the selected wells with numeric coercion.

    Placeholders such as DATA_UNRECORDED / MISSING_HARDWARE_SPEC become NaN.
    """
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
    out = out.sort_values(["WELL_NAME", "TEST_TS"], kind="mergesort").reset_index(drop=True)
    out["TEST_ID"] = out["WELL_NAME"] + " @ " + out["TEST_TS"].dt.strftime("%Y-%m-%d %H:%M")
    return out
