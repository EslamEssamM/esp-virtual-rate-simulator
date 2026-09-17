"""End-to-end pipeline: load -> flags -> mapping -> calibration -> validation -> rates -> events.

The pipeline is dataset-driven. `core.datasets` supplies the loaders and the few facts that
differ between fields; everything here is the same code for every dataset. `run_pipeline("GC31")`
reproduces the original behaviour exactly.

Every well in the dataset's `wells_all` is loaded from every input file. Wells in its
`excluded_wells`, plus any well with fewer than `MIN_MATCHED_TESTS` matched tests, keep their raw
rows and flags (so the Data quality page can show them) but are removed before calibration, so
they never reach K, MAPE, validation, events, the daily series, period statistics or the exports.
They are listed, with the reason, in `Results.excluded`.

Pure Python; the app layer wraps `run_pipeline` with a Streamlit cache. The flagged frame is
cached as parquet in cache/ (keyed on the dataset and the source-file signature) so later runs
skip the slow read.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from . import datasets as DS
from .calibration import calibrate, pip_baselines
from .diagnosis import detect_events
from .load import run_label
from .mapping import map_tests
from .pvt import gas_summary
from .quality import add_quality_flags, filter_summary, monthly_flag_counts
from .validation import mape_summary, mape_table, scope_comparison, validate
from .virtual_rate import compute_virtual_rate, daily_series, resample_rates


@dataclass
class Results:
    # --- loaded: every well, including excluded ones ---
    rt: pd.DataFrame                 # every SCADA row with flags, K, Q, PHI
    tests: pd.DataFrame              # all well tests for the loaded wells
    esp: pd.DataFrame                # per-well/per-test static data (source depends on dataset)
    runs: pd.DataFrame               # per-well pump metadata (install date where known)
    filter_summary: pd.DataFrame     # per-well flag counts (all wells, with an `excluded` column)
    monthly_flags: pd.DataFrame      # long table for the stacked bar (all wells)
    excluded: pd.DataFrame           # excluded wells with the reason
    lab_pvt: pd.DataFrame            # per-well lab PVT (empty when the field has none)
    test_pvt: pd.DataFrame           # per-test water cut, Bo and B_liq

    # --- analysed wells only ---
    mapped: pd.DataFrame             # tests + SCADA medians + MATCH status
    matched: pd.DataFrame            # matched tests with suspect flag
    cal: pd.DataFrame                # per (well, regime) calibration table
    validation: pd.DataFrame         # per-test predictions + APE
    mape: pd.DataFrame               # per scope x method, MAPE and MdAPE
    mape_overall: pd.DataFrame       # one row per method
    sensitivity: pd.DataFrame        # headline errors with / without the excluded wells
    daily: pd.DataFrame              # per-well calendar-day series
    hourly: pd.DataFrame             # hourly medians (steady rows)
    events: pd.DataFrame             # diagnosis event table
    pip_baselines: pd.DataFrame      # per (well, run) LOW_PIP_TREND baseline
    gas: pd.DataFrame                # per-well intake vs bubble point (empty without a Pb)
    regimes: pd.DataFrame            # per (well, regime) time bounds
    analyst: pd.DataFrame            # optional reference series, never used for calibration
    meta: dict = field(default_factory=dict)

    @property
    def dataset(self) -> DS.Dataset:
        return DS.get(self.meta.get("dataset", DS.DEFAULT_DATASET))

    @property
    def wells_analysed(self) -> list[str]:
        return list(self.meta.get("wells_analysed", []))

    @property
    def wells_all(self) -> list[str]:
        return list(self.meta.get("wells_all", []))


def _signature(ds: DS.Dataset) -> str:
    h = hashlib.md5()
    h.update(ds.key.encode())
    base = C.DATA_DIR if ds.key == DS.GC31.key else C.DATA_DIR / "meleiha"
    for name in ds.source_files:
        p = base / name
        if p.exists():
            st = p.stat()
            h.update(f"{name}:{st.st_size}:{int(st.st_mtime)}".encode())
    h.update(b"v4")  # bump when quality.py changes semantics
    return h.hexdigest()[:12]


def load_flagged_rt(ds: DS.Dataset, cache_dir: Path = C.CACHE_DIR,
                    force: bool = False) -> tuple[pd.DataFrame, dict]:
    """Flagged frame for every loaded well, from the parquet cache when the sources are unchanged."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    sig = _signature(ds)
    pq = cache_dir / f"rt_flagged_{ds.cache_key}_{sig}.parquet"
    meta = dict(cache_file=str(pq), from_cache=False)
    if pq.exists() and not force:
        d = pd.read_parquet(pq)
        if set(d["WELL_NAME"].unique()) >= set(ds.wells_all):
            meta["from_cache"] = True
            return d, meta
    d = add_quality_flags(ds.load_rt())
    if ds.quality_hook is not None:
        d = ds.quality_hook(d)
    for old in cache_dir.glob(f"rt_flagged_{ds.cache_key}_*.parquet"):
        if old != pq:
            old.unlink(missing_ok=True)
    d.to_parquet(pq, index=False)
    return d, meta


def excluded_table(ds: DS.Dataset, d: pd.DataFrame, tests: pd.DataFrame, runs: pd.DataFrame,
                   extra: dict[str, str] | None = None) -> pd.DataFrame:
    """One row per excluded well: the reason plus what was loaded for it."""
    reasons = {w: (ds.exclusion_reason(w), "config") for w in ds.excluded_wells}
    for w, why in (extra or {}).items():
        reasons.setdefault(w, (why, "insufficient data"))
    rows = []
    run_by_well = runs.set_index("WELL_NAME") if len(runs) else pd.DataFrame()
    for w, (reason, source) in reasons.items():
        g = d[d["WELL_NAME"] == w]
        r = run_by_well.loc[w] if w in getattr(run_by_well, "index", []) else None
        stages = r["NUMBER_OF_STAGES"] if r is not None else np.nan
        rows.append(dict(
            WELL_NAME=w, reason=reason, excluded_by=source,
            scada_rows=int(len(g)),
            scada_first=g["TIME_STAMP"].min() if len(g) else pd.NaT,
            scada_last=g["TIME_STAMP"].max() if len(g) else pd.NaT,
            well_tests=int((tests["WELL_NAME"] == w).sum()),
            pump=(f"{r['CANONICAL_MODEL']} x{int(stages)}" if r is not None and pd.notna(stages)
                  else (str(r["CANONICAL_MODEL"]) if r is not None else "")),
            manufacturer=r["PUMP_MANUFACTURER"] if r is not None else "",
        ))
    out = pd.DataFrame(rows)
    return out.sort_values("WELL_NAME").reset_index(drop=True) if len(out) else out


def run_pipeline(dataset: str | DS.Dataset = DS.DEFAULT_DATASET,
                 cache_dir: Path = C.CACHE_DIR, force: bool = False) -> Results:
    ds = DS.get(dataset)

    # ---------------------------------------------------------------- load (every well)
    d, meta = load_flagged_rt(ds, cache_dir, force)
    tests = ds.load_tests()
    esp = ds.load_static()
    runs = ds.load_runs(esp)
    if ds.key == DS.GC31.key:
        # GC31 carries pump-run metadata per test; the run label splits previous from current
        tests = tests.merge(esp[["WELL_NAME", "TEST_TS", "run", "DAYS_FROM_INSTALLATION"]],
                            on=["WELL_NAME", "TEST_TS"], how="left")
        tests["run"] = tests["run"].fillna("current")
    elif "run" not in tests.columns:
        tests["run"] = "current"

    lab_pvt = ds.load_lab_pvt() if ds.load_lab_pvt is not None else pd.DataFrame(columns=["WELL_NAME"])
    test_pvt = ds.load_test_pvt(tests) if ds.load_test_pvt is not None else pd.DataFrame()
    if ds.key == DS.GC31.key and len(test_pvt):
        tests = tests.merge(test_pvt, on=["WELL_NAME", "TEST_TS"], how="left")

    d["run"] = run_label(d["TIME_STAMP"], d["WELL_NAME"], runs)
    d["regime"] = ds.regime_hook(d, esp) if ds.regime_hook is not None else 1
    d["excluded"] = d["WELL_NAME"].isin(ds.excluded_wells)
    tests["excluded"] = tests["WELL_NAME"].isin(ds.excluded_wells)
    # a test inherits the regime of the SCADA rows around it
    tests = _tag_test_regime(tests, d)

    # ---------------------------------------------------------------- map and calibrate
    # Mapping and the robust-z screen are strictly per group, so mapping every loaded well and
    # then keeping the analysed ones gives exactly the same numbers as mapping them alone. The
    # all-well tables are used only for the exclusion sensitivity panel.
    mapped_all = map_tests(d, tests, windows=ds.map_windows)
    mm_all, cal_all = calibrate(mapped_all)
    v_all = validate(mm_all, tests)

    analysed = ds.analysed(d["WELL_NAME"].unique())
    keep = lambda df: df[df["WELL_NAME"].isin(analysed)].reset_index(drop=True)  # noqa: E731
    mapped, mm, cal, v = keep(mapped_all), keep(mm_all), keep(cal_all), keep(v_all)

    # a well the config kept but that cannot be calibrated is reported, never silently dropped
    thin = {w: (f"Only {int((mm['WELL_NAME'] == w).sum())} matched well test(s); at least "
                f"{C.MIN_MATCHED_TESTS} are needed to calibrate and validate K.")
            for w in analysed if w not in set(cal["WELL_NAME"])}
    if thin:
        analysed = [w for w in analysed if w not in thin]
        mapped, mm, cal, v = keep(mapped), keep(mm), keep(cal), keep(v)

    # ---------------------------------------------------------------- rates, events
    d = compute_virtual_rate(d, mm, cal, test_pvt, lab_pvt)   # excluded wells get no K, so no rate
    d_an = d[d["WELL_NAME"].isin(analysed)]
    daily = daily_series(d_an)
    hourly = resample_rates(d_an, "h", steady_only=True)
    bl = pip_baselines(mm, runs, d["TIME_STAMP"].min(), d["TIME_STAMP"].max())
    events = detect_events(d_an, daily, mm, bl)

    fs = filter_summary(d)
    fs["excluded"] = fs["WELL_NAME"].isin(ds.excluded_wells) | fs["WELL_NAME"].isin(thin)
    gas = gas_summary(d_an, lab_pvt) if ds.has_bubble_point and len(lab_pvt) else pd.DataFrame()
    regimes = (d.groupby(["WELL_NAME", "regime"])
               .agg(start=("TIME_STAMP", "min"), end=("TIME_STAMP", "max"), n_rows=("TIME_STAMP", "size"))
               .reset_index())
    analyst = ds.load_analyst() if ds.load_analyst is not None else pd.DataFrame()

    meta.update(
        dataset=ds.key, dataset_label=ds.label,
        n_rows=int(len(d)), n_rows_analysed=int(len(d_an)),
        n_tests=int(len(tests)), n_tests_analysed=int((~tests["excluded"]).sum()),
        n_matched=int(len(mm)), n_matched_all_wells=int(len(mm_all)),
        rt_start=d["TIME_STAMP"].min(), rt_end=d["TIME_STAMP"].max(),
        wells_all=sorted(d["WELL_NAME"].unique().tolist()), wells_analysed=analysed,
        n_regimes=int(regimes.groupby("WELL_NAME")["regime"].max().sum()),
    )
    return Results(
        rt=d, tests=tests, esp=esp, runs=runs,
        filter_summary=fs, monthly_flags=monthly_flag_counts(d),
        excluded=excluded_table(ds, d, tests, runs, thin),
        lab_pvt=lab_pvt, test_pvt=test_pvt,
        mapped=mapped, matched=mm, cal=cal, validation=v,
        mape=mape_table(v), mape_overall=mape_summary(v),
        sensitivity=scope_comparison(v, v_all, len(analysed), len(meta["wells_all"])),
        daily=daily, hourly=hourly, events=events, pip_baselines=bl, gas=gas,
        regimes=regimes, analyst=analyst, meta=meta,
    )


def _tag_test_regime(tests: pd.DataFrame, d: pd.DataFrame) -> pd.DataFrame:
    """Give each well test the regime of the nearest SCADA row of its well."""
    tests = tests.copy()
    tests["regime"] = 1
    if d["regime"].nunique() <= 1:
        return tests
    for w, g in d.groupby("WELL_NAME", sort=False):
        m = tests["WELL_NAME"] == w
        if not m.any():
            continue
        g = g.sort_values("TIME_STAMP")
        idx = np.searchsorted(g["TIME_STAMP"].to_numpy(), tests.loc[m, "TEST_TS"].to_numpy())
        idx = np.clip(idx, 0, len(g) - 1)
        tests.loc[m, "regime"] = g["regime"].to_numpy()[idx]
    return tests


def _round(df: pd.DataFrame, n: int) -> pd.DataFrame:
    num = df.select_dtypes("number").columns
    return df.assign(**{c: df[c].round(n) for c in num})


def results_summary(res: Results) -> str:
    """Plain-text summary for the CLI / README."""
    lines = [f"== dataset: {res.meta['dataset_label']} ==",
             "", "== excluded wells ==",
             (res.excluded[["WELL_NAME", "excluded_by", "scada_rows", "well_tests", "pump"]].to_string(index=False)
              if len(res.excluded) else "(none)"),
             "", "== filter summary (all loaded wells) ==", res.filter_summary.to_string(index=False),
             "", "== regimes ==", res.regimes.to_string(index=False),
             "", "== calibration ==", _round(res.cal, 3).to_string(index=False),
             "", "== matched tests ==",
             _round(res.matched[["WELL_NAME", "regime", "TEST_TS", "Q_LIQ", "RT_X", "K", "PIP_diff_vs_test", "robust_z", "suspect"]], 3).to_string(index=False),
             "", "== MAPE / MdAPE ==", _round(res.mape_overall, 2).to_string(index=False),
             "", "== MAPE per well ==",
             _round(res.mape[res.mape["scope"] != "ALL"][["scope", "method", "MAPE_all", "MdAPE_all", "n_all", "MAPE_excl_suspect", "n_excl_suspect"]], 2).to_string(index=False),
             "", "== events ==", res.events.groupby(["WELL_NAME", "type"]).size().to_string(),
             "", json.dumps({k: str(v) for k, v in res.meta.items()}, indent=1)]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    print(results_summary(run_pipeline(args[0] if args else DS.DEFAULT_DATASET,
                                       force="--force" in sys.argv)))
