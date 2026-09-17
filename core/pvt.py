"""PVT: water-cut correction of the rate and the free-gas-at-intake indicator (spec addendum).

Two independent things live here.

**A. Liquid formation volume factor.** The power equation returns the rate at PUMP conditions
(reservoir bbl/d); well tests are measured at surface (stock-tank bbl/d). Calibrating K straight
to a surface test therefore hides a factor 1/B_liq at the calibration water cut, which drifts as
water cut rises. Separating them:

    B_liq = WC * Bw + (1 - WC) * Bo          Bw = 1.020 rb/stb, constant
    K_dh  = Q_test * B_liq_test / X_test     downhole-basis lumped factor
    Q_M6  = K_dh * X(t) / B_liq(t)           surface rate

WC and Bo come per test from ESP_MASTER_DATASET.csv and are interpolated in time between the
well's tests, flat before the first and after the last.

**B. Free gas at the intake.** Per-well lab PVT (bubble point, solution GOR, API, reservoir
temperature) from pvtdetails_4wells.csv, compared with the measured intake pressure. The gas
volume fraction is INDICATIVE only: gas gravity is not in any data file and is assumed to be
0.80, with Z = 0.9. The sign and order of magnitude are solid, the exact percentage is not.
Never read P56 / P58 / P59 from the 81-parameter CSV for this: they are dataset-wide
placeholders, not per-well values.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C

LAB_COLUMNS = ["PB", "RS", "OIL_FVF", "OIL_VISCOSITY", "DENSITY", "API", "RESERVOIR_TEMP"]
PVT_COLS = ["WC_FRAC", "BO", "B_LIQ", "RS_TEST"]


# --------------------------------------------------------------------------- loading

def load_lab_pvt(path: Path | str = C.PVT_FILE, wells: list[str] | None = None) -> pd.DataFrame:
    """One row per well of lab PVT: Pb, Rs, Bo, oil viscosity, oil SG, API, reservoir T."""
    wells = C.WELLS_ALL if wells is None else wells
    t = pd.read_csv(path)
    t = t[t["WELL_NAME"].isin(wells)].copy()
    for c in LAB_COLUMNS:
        t[c] = pd.to_numeric(t[c], errors="coerce")
    return t[["WELL_NAME", *LAB_COLUMNS]].sort_values("WELL_NAME").reset_index(drop=True)


def load_test_pvt(path: Path | str = C.ESP_MASTER_FILE, wells: list[str] | None = None) -> pd.DataFrame:
    """Per-test water cut, Bo and B_liq from the ESP master dataset.

    `B_LIQ_FILE` is the file's own B_LIQ_RBSTB, kept so the computed value can be checked
    against it.
    """
    wells = C.WELLS_ALL if wells is None else wells
    t = pd.read_csv(path, low_memory=False)
    t = t[t["WELL_NAME"].isin(wells)].copy()
    out = pd.DataFrame({
        "WELL_NAME": t["WELL_NAME"].astype(str).values,
        "TEST_TS": pd.to_datetime(t["TEST_DATE_TIME"]).values,
        "WC_FRAC": pd.to_numeric(t["WATER_CUT_PCT"], errors="coerce").values / 100.0,
        "BO": pd.to_numeric(t["B_O_RBSTB"], errors="coerce").values,
        "RS_TEST": pd.to_numeric(t["RS_SCFBBL"], errors="coerce").values if "RS_SCFBBL" in t else np.nan,
        "B_LIQ_FILE": pd.to_numeric(t["B_LIQ_RBSTB"], errors="coerce").values if "B_LIQ_RBSTB" in t else np.nan,
    })
    out["B_LIQ"] = b_liq(out["WC_FRAC"], out["BO"])
    return out.sort_values(["WELL_NAME", "TEST_TS"], kind="mergesort").reset_index(drop=True)


# --------------------------------------------------------------------------- A. B_liq

def b_liq(wc, bo, bw: float = C.BW):
    """Liquid formation volume factor, rb/stb. `wc` is a fraction (0-1), not a percentage."""
    return wc * bw + (1.0 - wc) * bo


def interpolate_pvt(test_pvt: pd.DataFrame, well: str, timestamps, cols=PVT_COLS) -> pd.DataFrame:
    """PVT properties at arbitrary timestamps: linear in time between the well's tests, flat
    before the first and after the last. Wells with no test PVT give NaN."""
    ts = pd.to_datetime(pd.Series(timestamps))
    out = pd.DataFrame(index=getattr(timestamps, "index", ts.index))
    g = test_pvt[test_pvt["WELL_NAME"] == well]
    tnum = ts.astype("datetime64[ns]").astype("int64").to_numpy()
    for c in cols:
        s = g.dropna(subset=[c]).sort_values("TEST_TS") if c in g.columns else g.iloc[0:0]
        if s.empty:
            out[c] = np.nan
        else:
            out[c] = np.interp(tnum, s["TEST_TS"].astype("datetime64[ns]").astype("int64").to_numpy(),
                               s[c].to_numpy())
    return out


# --------------------------------------------------------------------------- B. free gas

def standing_rs(p, api, t_f, gas_gravity: float = C.GAS_GRAVITY):
    """Standing (1947) solution GOR, scf/stb. Gas gravity is assumed (not in the data)."""
    return gas_gravity * ((np.asarray(p, dtype=float) / 18.2 + 1.4)
                          * 10 ** (0.0125 * api - 0.00091 * t_f)) ** 1.2048


def free_gas_indicator(lab: pd.Series | dict, pip, wc, bo, bw: float = C.BW,
                       z: float = C.Z_FACTOR) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(PIP - Pb, free gas scf/stb, in-situ gas volume fraction at the intake).

    Rs(p) is Standing's correlation scaled so that Rs(Pb) equals the lab Rs at the bubble point;
    whatever does not stay in solution at the intake pressure is free gas. The GVF is indicative:
    see the module docstring.
    """
    pb, rsb, api, t_f = float(lab["PB"]), float(lab["RS"]), float(lab["API"]), float(lab["RESERVOIR_TEMP"])
    pip = np.asarray(pip, dtype=float)
    wc = np.asarray(wc, dtype=float)
    bo = np.asarray(bo, dtype=float)
    scale = rsb / standing_rs(pb, api, t_f)
    rs_p = np.minimum(standing_rs(pip, api, t_f), rsb) * scale
    free = np.clip(rsb - rs_p, 0, None)                       # scf/stb liberated at the intake
    bg = 0.0283 * z * (t_f + 460.0) / pip                     # ft3/scf
    vg = free * bg / 5.615                                    # rb of gas per stb of oil
    oil, water = (1 - wc) * vg, wc * bw
    gvf = oil / (oil + (1 - wc) * bo + water)
    return pip - pb, free, gvf


def add_pvt_columns(d: pd.DataFrame, test_pvt: pd.DataFrame, lab: pd.DataFrame | None = None,
                    wells: list[str] | None = None) -> pd.DataFrame:
    """Add WC_FRAC, BO, B_LIQ, Pb, PIP_minus_Pb, below_pb, free_gas_scf_stb and gvf_est to the
    SCADA frame. Every row is kept; wells without PVT get NaN."""
    d = d.copy()
    for c in ["WC_FRAC", "BO", "B_LIQ", "RS_TEST", "Pb", "PIP_minus_Pb", "free_gas_scf_stb", "gvf_est"]:
        d[c] = np.nan
    lab = pd.DataFrame(columns=["WELL_NAME", *LAB_COLUMNS]) if lab is None else lab
    lab_by_well = lab.set_index("WELL_NAME") if len(lab) else pd.DataFrame()
    todo = d["WELL_NAME"].unique() if wells is None else [w for w in wells if w in set(d["WELL_NAME"])]
    for w in todo:
        m = d["WELL_NAME"] == w
        g = d.loc[m]
        p = interpolate_pvt(test_pvt, w, g["TIME_STAMP"])
        for c in PVT_COLS:
            d.loc[m, c] = p[c].to_numpy()
        if len(lab_by_well) and w in lab_by_well.index:
            lr = lab_by_well.loc[w]
            dpb, free, gvf = free_gas_indicator(lr, g["PIP"].to_numpy(), p["WC_FRAC"].to_numpy(), p["BO"].to_numpy())
            d.loc[m, "Pb"] = float(lr["PB"])
            d.loc[m, "PIP_minus_Pb"] = dpb
            d.loc[m, "free_gas_scf_stb"] = free
            d.loc[m, "gvf_est"] = gvf
    d["below_pb"] = d["PIP_minus_Pb"] < 0
    return d


def gas_summary(d: pd.DataFrame, lab: pd.DataFrame, mask: str = "rate_steady") -> pd.DataFrame:
    """Per-well free-gas summary over the rate-bearing rows: PIP median, PIP - Pb median,
    % of rows below the bubble point, and the estimated GVF median and p95."""
    rows = []
    sel = d[d[mask]] if mask in d.columns else d
    lab = pd.DataFrame(columns=["WELL_NAME", *LAB_COLUMNS]) if lab is None else lab
    lab_by_well = lab.set_index("WELL_NAME") if len(lab) else pd.DataFrame()
    for w, g in sel.groupby("WELL_NAME"):
        g = g.dropna(subset=["PIP_minus_Pb"])
        if g.empty:
            continue
        lr = lab_by_well.loc[w] if w in lab_by_well.index else {}
        rows.append(dict(
            WELL_NAME=w,
            Pb=float(lr.get("PB", np.nan)), Rs_b=float(lr.get("RS", np.nan)),
            oil_visc_cp=float(lr.get("OIL_VISCOSITY", np.nan)), API=float(lr.get("API", np.nan)),
            PIP_median=float(g["PIP"].median()),
            PIP_minus_Pb_median=float(g["PIP_minus_Pb"].median()),
            pct_rows_below_Pb=float(g["below_pb"].mean() * 100),
            gvf_median_pct=float(g["gvf_est"].median() * 100),
            gvf_p95_pct=float(g["gvf_est"].quantile(0.95) * 100),
            n_rows=int(len(g)),
        ))
    return pd.DataFrame(rows)


def gas_severity(pip_minus_pb: float) -> str:
    """'ok' above the bubble point, 'watch' within the margin below it, 'gassy' further below."""
    if pip_minus_pb is None or not np.isfinite(pip_minus_pb):
        return "unknown"
    if pip_minus_pb > 0:
        return "ok"
    return "watch" if pip_minus_pb >= -C.GAS_AT_INTAKE_MARGIN else "gassy"
