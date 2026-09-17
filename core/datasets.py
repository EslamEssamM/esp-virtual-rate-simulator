"""Dataset registry: one entry per field, all sharing the same physics and pipeline.

A `Dataset` supplies the loaders and the handful of facts that differ between fields (well list,
excluded wells, electrical basis, mapping window, whether a bubble point or per-test Bo exists).
Everything downstream - quality flags, mapping, calibration, validation, rates, diagnosis - is
the same code for every dataset.

Adding a field means adding a loader module and one entry here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from . import config as C
from . import meleiha as ML
from . import load as GC
from . import pvt as PVT


@dataclass(frozen=True)
class ElecBasis:
    """Which tags the power equation is fed from, and what that implies for K."""
    voltage_tag: str
    current_tag: str
    note: str = ""
    power_factor_logged: bool = False


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    field_note: str
    wells_all: list[str]
    excluded_wells: dict[str, str]

    load_rt: Callable[[], pd.DataFrame]
    load_tests: Callable[[], pd.DataFrame]
    load_static: Callable[[], pd.DataFrame]
    load_runs: Callable[[pd.DataFrame], pd.DataFrame]
    load_lab_pvt: Callable[[], pd.DataFrame] | None
    load_test_pvt: Callable[[pd.DataFrame], pd.DataFrame] | None
    load_analyst: Callable[[], pd.DataFrame] | None = None

    basis: ElecBasis = field(default_factory=lambda: ElecBasis("VOLTAGE", "AMPERAGE"))
    # mapping windows tried in order; a dataset whose tests carry only a date skips the narrow one
    map_windows: tuple[int, ...] = (C.MAP_WINDOW_H, C.MAP_WINDOW_WIDE_H)
    quality_hook: Callable[[pd.DataFrame], pd.DataFrame] | None = None
    regime_hook: Callable[[pd.DataFrame, pd.DataFrame], pd.Series] | None = None
    has_bubble_point: bool = True
    wc_correction_note: str = ""
    source_files: tuple[str, ...] = ()
    cache_key: str = ""

    # ---- well scope, mirroring config.py for the default dataset ----
    @property
    def wells(self) -> list[str]:
        return [w for w in self.wells_all if w not in self.excluded_wells]

    def is_excluded(self, well: str) -> bool:
        return well in self.excluded_wells

    def exclusion_reason(self, well: str) -> str:
        return self.excluded_wells.get(well, "")

    def analysed(self, wells) -> list[str]:
        s = set(wells)
        return [w for w in self.wells_all if w in s and w not in self.excluded_wells]


# --------------------------------------------------------------------------- GC31 (default)

GC31 = Dataset(
    key="GC31",
    label="GC31 (Kuwait, 4 wells)",
    field_note="30-min SCADA, Apr-2024 to Jul-2026. Voltage tag switches between drive side (LV) "
               "and motor side (MV) over time.",
    wells_all=C.WELLS_ALL,
    excluded_wells=C.EXCLUDED_WELLS,
    load_rt=GC.load_rt,
    load_tests=GC.load_tests,
    load_static=lambda: GC.load_esp_master(),
    load_runs=lambda esp: GC.pump_runs(esp),
    load_lab_pvt=PVT.load_lab_pvt,
    load_test_pvt=lambda _tests: PVT.load_test_pvt(),
    basis=ElecBasis("VOLTAGE", "AMPERAGE",
                    "The VOLTAGE tag changes basis over time (LV drive side below 800 V, MV motor "
                    "side above). K is only valid on the basis it was calibrated on."),
    source_files=(C.RT_FILE.name, C.WT_FILE.name, C.ESP_MASTER_FILE.name, C.PVT_FILE.name),
    cache_key="gc31",
)


# --------------------------------------------------------------------------- Meleiha

MELEIHA = Dataset(
    key="Meleiha",
    label="Meleiha (Egypt, 5 wells)",
    field_note="1-10 min SCADA, Oct-2018 to Nov-2021. Both voltage tags are drive-side; the "
               "step-up transformer ratio is absorbed into K.",
    wells_all=ML.WELLS,
    excluded_wells={},                     # none by default; the < 2 matched tests rule still applies
    load_rt=ML.load_rt,
    load_tests=ML.load_tests,
    load_static=ML.load_static,
    load_runs=ML.load_runs,
    load_lab_pvt=None,                     # no bubble point exists for these wells
    load_test_pvt=ML.test_pvt,
    load_analyst=ML.load_analyst,
    basis=ElecBasis("VOLTAGE_VSD_OUT", "AMPERAGE_MOTOR", ML.BASIS_NOTE),
    map_windows=(C.MAP_WINDOW_WIDE_H,),    # tests carry a date only, so go straight to +/-24 h
    quality_hook=ML.apply_quality,
    regime_hook=ML.detect_regimes,
    has_bubble_point=False,
    wc_correction_note=ML.ASSUMED_BO_NOTE,
    source_files=(ML.RT_FILE.name, ML.TESTS_FILE.name, ML.STATIC_FILE.name,
                  ML.QUALITY_FILE.name, ML.ANALYST_FILE.name),
    cache_key="meleiha",
)


DATASETS: dict[str, Dataset] = {GC31.key: GC31, MELEIHA.key: MELEIHA}
DEFAULT_DATASET = GC31.key


def get(key: str | Dataset | None = None) -> Dataset:
    """Dataset by key, defaulting to GC31. Passing a Dataset through is allowed."""
    if isinstance(key, Dataset):
        return key
    return DATASETS[key or DEFAULT_DATASET]


def available() -> list[str]:
    """Keys of the datasets whose files are present on disk."""
    out = []
    for k, ds in DATASETS.items():
        base = C.DATA_DIR if ds.key == GC31.key else ML.DATA_DIR
        if all((base / f).exists() for f in ds.source_files):
            out.append(k)
    return out or [DEFAULT_DATASET]
