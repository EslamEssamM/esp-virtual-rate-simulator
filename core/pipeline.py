"""End-to-end pipeline: load -> flags -> mapping -> calibration -> validation -> rates -> events.

Every well in `config.WELLS_ALL` is loaded from all three input files. Wells in
`config.EXCLUDED_WELLS` keep their raw rows and flags (so the Data quality page can show them)
but are removed before calibration, so they never reach K, MAPE, validation, events, the daily
series, period statistics or the exports. They are listed, with the verbatim reason, in
`Results.excluded`.

Pure Python; the app layer wraps `run_pipeline` with a Streamlit cache. The flagged SCADA frame
is cached as parquet in cache/ (keyed on the source-file signature) so later runs skip the
Excel read.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import config as C
from .calibration import calibrate, pip_baselines
from .diagnosis import detect_events
from .load import load_esp_master, load_rt, load_tests, pump_runs, run_label
from .mapping import map_tests
from .quality import add_quality_flags, filter_summary, monthly_flag_counts
from .validation import mape_summary, mape_table, scope_comparison, validate
from .virtual_rate import compute_virtual_rate, daily_series, resample_rates


@dataclass
class Results:
    # --- loaded: every well, including excluded ones ---
    rt: pd.DataFrame                 # every SCADA row with flags, K, Q, PHI
    tests: pd.DataFrame              # all well tests for the loaded wells
    esp: pd.DataFrame                # per-test pump-run metadata (ESP master dataset)
    runs: pd.DataFrame               # per-well current pump run (install date, pump, stages)
    filter_summary: pd.DataFrame     # per-well flag counts (all wells, with an `excluded` column)
    monthly_flags: pd.DataFrame      # long table for the stacked bar (all wells)
    excluded: pd.DataFrame           # excluded wells with the verbatim reason

    # --- analysed wells only ---
    mapped: pd.DataFrame             # tests + SCADA medians + MATCH status
    matched: pd.DataFrame            # matched tests with suspect flag
    cal: pd.DataFrame                # per-well calibration table
    validation: pd.DataFrame         # per-test predictions + APE
    mape: pd.DataFrame               # per scope x method, MAPE and MdAPE
    mape_overall: pd.DataFrame       # 3-row summary
    sensitivity: pd.DataFrame        # headline errors with / without the excluded wells
    daily: pd.DataFrame              # per-well calendar-day series
    hourly: pd.DataFrame             # hourly medians (steady rows)
    events: pd.DataFrame             # diagnosis event table
    pip_baselines: pd.DataFrame      # per (well, run) LOW_PIP_TREND baseline
    meta: dict = field(default_factory=dict)

    @property
    def wells_analysed(self) -> list[str]:
        return list(self.meta.get("wells_analysed", []))

    @property
    def wells_all(self) -> list[str]:
        return list(self.meta.get("wells_all", []))


def _signature(paths: list[Path]) -> str:
    h = hashlib.md5()
    for p in paths:
        st = Path(p).stat()
        h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    h.update(b"v3")  # bump when quality.py changes semantics
    return h.hexdigest()[:12]


def load_flagged_rt(rt_file: Path = C.RT_FILE, cache_dir: Path = C.CACHE_DIR,
                    force: bool = False) -> tuple[pd.DataFrame, dict]:
    """Flagged SCADA frame for every loaded well, from the parquet cache when unchanged."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    sig = _signature([rt_file])
    pq = cache_dir / f"rt_flagged_{sig}.parquet"
    meta = dict(cache_file=str(pq), from_cache=False)
    if pq.exists() and not force:
        d = pd.read_parquet(pq)
        if set(d["WELL_NAME"].unique()) >= set(C.WELLS_ALL):
            meta["from_cache"] = True
            return d, meta
    d = add_quality_flags(load_rt(rt_file))
    for old in cache_dir.glob("rt_flagged_*.parquet"):
        if old != pq:
            old.unlink(missing_ok=True)
    d.to_parquet(pq, index=False)
    return d, meta


def excluded_table(d: pd.DataFrame, tests: pd.DataFrame, runs: pd.DataFrame,
                   extra: dict[str, str] | None = None) -> pd.DataFrame:
    """One row per excluded well: verbatim reason plus what was loaded for it.

    `extra` carries wells excluded by the pipeline itself (too few matched tests) rather than
    by configuration.
    """
    reasons = {w: (C.well_exclusion_reason(w), "config") for w in C.EXCLUDED_WELLS}
    for w, why in (extra or {}).items():
        reasons.setdefault(w, (why, "insufficient data"))
    rows = []
    run_by_well = runs.set_index("WELL_NAME") if len(runs) else pd.DataFrame()
    for w, (reason, source) in reasons.items():
        g = d[d["WELL_NAME"] == w]
        r = run_by_well.loc[w] if w in getattr(run_by_well, "index", []) else None
        rows.append(dict(
            WELL_NAME=w, reason=reason, excluded_by=source,
            scada_rows=int(len(g)),
            scada_first=g["TIME_STAMP"].min() if len(g) else pd.NaT,
            scada_last=g["TIME_STAMP"].max() if len(g) else pd.NaT,
            well_tests=int((tests["WELL_NAME"] == w).sum()),
            pump=f"{r['CANONICAL_MODEL']} x{int(r['NUMBER_OF_STAGES'])}" if r is not None else "",
            manufacturer=r["PUMP_MANUFACTURER"] if r is not None else "",
        ))
    return pd.DataFrame(rows).sort_values("WELL_NAME").reset_index(drop=True)


def run_pipeline(rt_file: Path = C.RT_FILE, wt_file: Path = C.WT_FILE, esp_file: Path = C.ESP_MASTER_FILE,
                 cache_dir: Path = C.CACHE_DIR, force: bool = False) -> Results:
    # ---------------------------------------------------------------- load (every well)
    d, meta = load_flagged_rt(rt_file, cache_dir, force)
    tests = load_tests(wt_file)
    esp = load_esp_master(esp_file)
    runs = pump_runs(esp)
    tests = tests.merge(esp[["WELL_NAME", "TEST_TS", "run", "DAYS_FROM_INSTALLATION"]],
                        on=["WELL_NAME", "TEST_TS"], how="left")
    tests["run"] = tests["run"].fillna("current")
    d["run"] = run_label(d["TIME_STAMP"], d["WELL_NAME"], runs)
    d["excluded"] = d["WELL_NAME"].isin(C.EXCLUDED_WELLS)
    tests["excluded"] = tests["WELL_NAME"].isin(C.EXCLUDED_WELLS)

    # ---------------------------------------------------------------- map and calibrate
    # Mapping and the robust-z screen are strictly per well, so mapping every loaded well and
    # then keeping the analysed ones gives exactly the same numbers as mapping them alone. The
    # all-well tables are used only for the exclusion sensitivity panel.
    mapped_all = map_tests(d, tests)
    mm_all, cal_all = calibrate(mapped_all)
    v_all = validate(mm_all, tests)

    analysed = C.analysed(d["WELL_NAME"].unique())
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
    d = compute_virtual_rate(d, mm, cal)          # excluded wells get no K, so no rate
    d_an = d[d["WELL_NAME"].isin(analysed)]
    daily = daily_series(d_an)
    hourly = resample_rates(d_an, "h", steady_only=True)
    bl = pip_baselines(mm, runs, d["TIME_STAMP"].min(), d["TIME_STAMP"].max())
    events = detect_events(d_an, daily, mm, bl)

    fs = filter_summary(d)
    fs["excluded"] = fs["WELL_NAME"].isin(C.EXCLUDED_WELLS) | fs["WELL_NAME"].isin(thin)

    meta.update(
        n_rows=int(len(d)), n_rows_analysed=int(len(d_an)),
        n_tests=int(len(tests)), n_tests_analysed=int((~tests["excluded"]).sum()),
        n_matched=int(len(mm)), n_matched_all_wells=int(len(mm_all)),
        rt_start=d["TIME_STAMP"].min(), rt_end=d["TIME_STAMP"].max(),
        wells_all=sorted(d["WELL_NAME"].unique().tolist()), wells_analysed=analysed,
    )
    return Results(
        rt=d, tests=tests, esp=esp, runs=runs,
        filter_summary=fs, monthly_flags=monthly_flag_counts(d),
        excluded=excluded_table(d, tests, runs, thin),
        mapped=mapped, matched=mm, cal=cal, validation=v,
        mape=mape_table(v), mape_overall=mape_summary(v),
        sensitivity=scope_comparison(v, v_all, len(analysed), len(meta["wells_all"])),
        daily=daily, hourly=hourly, events=events, pip_baselines=bl, meta=meta,
    )


def _round(df: pd.DataFrame, n: int) -> pd.DataFrame:
    num = df.select_dtypes("number").columns
    return df.assign(**{c: df[c].round(n) for c in num})


def results_summary(res: Results) -> str:
    """Plain-text summary for the CLI / README."""
    lines = ["== excluded wells ==", res.excluded[["WELL_NAME", "excluded_by", "scada_rows", "well_tests", "pump"]].to_string(index=False),
             "", "== filter summary (all loaded wells) ==", res.filter_summary.to_string(index=False),
             "", "== calibration ==", _round(res.cal, 3).to_string(index=False),
             "", "== matched tests ==",
             _round(res.matched[["WELL_NAME", "TEST_TS", "Q_LIQ", "RT_X", "K", "PIP_diff_vs_test", "robust_z", "suspect"]], 3).to_string(index=False),
             "", "== validation ==", _round(res.validation, 1).to_string(index=False),
             "", "== MAPE / MdAPE ==", _round(res.mape_overall, 2).to_string(index=False),
             "", "== sensitivity to the exclusion and test-set rules ==",
             _round(res.sensitivity.drop(columns=["method"]), 2).to_string(index=False),
             "", "== pump runs ==", res.runs.to_string(index=False),
             "", "== events ==", res.events.groupby(["WELL_NAME", "type"]).size().to_string(),
             "", json.dumps({k: str(v) for k, v in res.meta.items()}, indent=1)]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    print(results_summary(run_pipeline(force="--force" in sys.argv)))
