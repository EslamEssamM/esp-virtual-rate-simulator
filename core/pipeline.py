"""End-to-end pipeline: load -> flags -> mapping -> calibration -> validation -> rates -> events.

Pure Python; the app layer wraps `run_pipeline` with st.cache_data. The flagged SCADA frame
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
from .calibration import calibrate
from .diagnosis import detect_events
from .load import load_rt, load_tests
from .mapping import map_tests
from .quality import add_quality_flags, filter_summary, monthly_flag_counts
from .validation import mape_summary, mape_table, validate
from .virtual_rate import compute_virtual_rate, daily_series, resample_rates


@dataclass
class Results:
    rt: pd.DataFrame                 # every SCADA row with flags, K, Q, PHI
    tests: pd.DataFrame              # all well tests for the 4 wells
    mapped: pd.DataFrame             # tests + SCADA medians + MATCH status
    matched: pd.DataFrame            # matched tests with suspect flag
    cal: pd.DataFrame                # per-well calibration table
    validation: pd.DataFrame         # per-test predictions + APE
    mape: pd.DataFrame               # per scope x method
    mape_overall: pd.DataFrame       # 3-row summary
    daily: pd.DataFrame              # per-well calendar-day series
    hourly: pd.DataFrame             # hourly medians (steady rows)
    events: pd.DataFrame             # diagnosis event table
    filter_summary: pd.DataFrame     # per-well flag counts
    monthly_flags: pd.DataFrame      # long table for the stacked bar
    meta: dict = field(default_factory=dict)


def _signature(paths: list[Path]) -> str:
    h = hashlib.md5()
    for p in paths:
        st = Path(p).stat()
        h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    h.update(b"v3")  # bump when quality.py changes semantics
    return h.hexdigest()[:12]


def load_flagged_rt(rt_file: Path = C.RT_FILE, cache_dir: Path = C.CACHE_DIR, force: bool = False) -> tuple[pd.DataFrame, dict]:
    """Flagged SCADA frame, from the parquet cache when the source is unchanged."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    sig = _signature([rt_file])
    pq = cache_dir / f"rt_flagged_{sig}.parquet"
    meta = dict(cache_file=str(pq), from_cache=False)
    if pq.exists() and not force:
        d = pd.read_parquet(pq)
        meta["from_cache"] = True
        return d, meta
    d = add_quality_flags(load_rt(rt_file))
    for old in cache_dir.glob("rt_flagged_*.parquet"):
        if old != pq:
            old.unlink(missing_ok=True)
    d.to_parquet(pq, index=False)
    return d, meta


def run_pipeline(rt_file: Path = C.RT_FILE, wt_file: Path = C.WT_FILE,
                 cache_dir: Path = C.CACHE_DIR, force: bool = False) -> Results:
    d, meta = load_flagged_rt(rt_file, cache_dir, force)
    tests = load_tests(wt_file)
    mapped = map_tests(d, tests)
    mm, cal = calibrate(mapped)
    v = validate(mm, tests)
    d = compute_virtual_rate(d, mm, cal)
    daily = daily_series(d)
    hourly = resample_rates(d, "h", steady_only=True)
    events = detect_events(d, daily, mm, cal)
    meta.update(
        n_rows=int(len(d)), n_tests=int(len(tests)), n_matched=int(len(mm)),
        rt_start=d["TIME_STAMP"].min(), rt_end=d["TIME_STAMP"].max(),
        wells=sorted(d["WELL_NAME"].unique().tolist()),
    )
    return Results(rt=d, tests=tests, mapped=mapped, matched=mm, cal=cal, validation=v,
                   mape=mape_table(v), mape_overall=mape_summary(v), daily=daily, hourly=hourly,
                   events=events, filter_summary=filter_summary(d), monthly_flags=monthly_flag_counts(d),
                   meta=meta)


def _round(df: pd.DataFrame, n: int) -> pd.DataFrame:
    num = df.select_dtypes("number").columns
    return df.assign(**{c: df[c].round(n) for c in num})


def results_summary(res: Results) -> str:
    """Plain-text summary for the CLI / README."""
    lines = ["== filter summary ==", res.filter_summary.to_string(index=False),
             "", "== calibration ==", _round(res.cal, 3).to_string(index=False),
             "", "== matched tests ==",
             _round(res.matched[["WELL_NAME", "TEST_TS", "Q_LIQ", "RT_X", "K", "PIP_diff_vs_test", "robust_z", "suspect"]], 3).to_string(index=False),
             "", "== validation ==", _round(res.validation, 1).to_string(index=False),
             "", "== MAPE ==", _round(res.mape_overall, 2).to_string(index=False),
             "", "== events ==", res.events.groupby(["WELL_NAME", "type"]).size().to_string(),
             "", json.dumps({k: str(v) for k, v in res.meta.items()}, indent=1)]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    print(results_summary(run_pipeline(force="--force" in sys.argv)))
