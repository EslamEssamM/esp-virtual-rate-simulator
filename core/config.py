"""Constants shared by the core modules."""
import hashlib
from dataclasses import dataclass, fields
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
CACHE_DIR = APP_DIR / "cache"

RT_FILE = DATA_DIR / "AI_VW_REAL_TIME_DATA_Sample_Date_13-Sep-2026 V 1.1.xlsx"
RT_SHEET = "AI_VW_REAL_TIME_DATA"
WT_FILE = DATA_DIR / "GC31_DIGIWELLS_81_PARAM_MASTER_DATASET.csv"

# --- wells -----------------------------------------------------------------
# Every well in WELLS_ALL is LOADED from all three input files. Wells listed in
# EXCLUDED_WELLS are loaded and displayed (greyed out, with the reason) but never enter
# calibration, K, MAPE, validation, events, period statistics, cumulative liquid or the
# exports - apart from excluded_wells.csv, which lists them with their reason.
# Removing a well from EXCLUDED_WELLS is the only change needed to analyse it: no other
# module refers to a well by name.
WELLS_ALL = ["SA-0162_T", "SA-0500_T", "SA-0512H_T", "SA-0991H_T"]

EXCLUDED_WELLS = {
    "SA-0991H_T": (
        "Real-time data cover only Apr-May 2024 (previous pump run) and match a single well "
        "test; K cannot be validated, no leave-one-out, no PHI trend. New WG-4000 run "
        "installed Jun-2026 has no SCADA yet."
    ),
}

WELLS = [w for w in WELLS_ALL if w not in EXCLUDED_WELLS]

# Minimum matched tests a well needs before it can be calibrated and validated.
MIN_MATCHED_TESTS = 2

# Calibration group key. A well normally has one electrical regime, so `regime` is 1 and this is
# equivalent to grouping by well. A well whose transformer ratio or stage count changed mid-life
# (Meleiha M-80 ST) is split into regimes and gets one K per regime.
GROUP = ["WELL_NAME", "regime"]


def is_excluded(well: str) -> bool:
    return well in EXCLUDED_WELLS


def well_exclusion_reason(well: str) -> str:
    """Verbatim reason a well is excluded, or '' when it is analysed."""
    return EXCLUDED_WELLS.get(well, "")


def analysed(wells) -> list[str]:
    """The analysed subset of `wells`, in WELLS_ALL order."""
    s = set(wells)
    return [w for w in WELLS_ALL if w in s and w not in EXCLUDED_WELLS]

# --- quality thresholds (spec section 2) ---
PUMP_OFF_V = 100.0
PUMP_OFF_I = 5.0
MIN_DP = 300.0
PIP_RANGE = (50.0, 5000.0)
# No PDP range gate: discharge pressure spans too wide a range across fields to bound sensibly.
# A bad PDP still fails the dP rule (dP <= 300 psi) or the WHP > PDP check.
FREQ_RANGE = (30.0, 70.0)
TRANSIENT_WINDOW = 6
TRANSIENT_MIN_PERIODS = 3
TRANSIENT_CV = 0.05
TEMP_C_MT_MAX = 150.0        # MT below this -> logged in degC
TEMP_C_IT_MAX = 100.0        # INTAKE_TEMP below this -> logged in degC
LV_MV_SPLIT_V = 800.0
ELEC_BASIS_LO = 0.8
ELEC_BASIS_HI = 1.2
FREQ_FFILL_LIMIT_H = 24


@dataclass(frozen=True)
class Thresholds:
    """Every constraint the app puts on a SCADA row, in one immutable object.

    The constants above are the defaults and are what the README and the test suite pin down.
    The Filter rules page builds a different `Thresholds` from user input and runs the whole
    pipeline on it, so a rule can be loosened or switched off and its effect on K, on the error
    against the well tests and on the row counts is visible rather than assumed.

    Frozen, so it can key a cache; all fields are plain numbers and booleans.
    """
    min_dp: float = MIN_DP
    pip_lo: float = PIP_RANGE[0]
    pip_hi: float = PIP_RANGE[1]
    # No PDP ceiling by default: discharge pressure spans too wide a range across fields to bound
    # sensibly, so the rule is offered rather than imposed. 0 means no ceiling. It is the lever for
    # a gauge pegged at its rail - Meleiha M-80 ST sits at 6554 psi (a 16-bit limit) for 56,835
    # rows whose dP is 4700-5800 psi, so the dP floor cannot catch them.
    pdp_max: float = 0.0
    whp_over_pdp: bool = True
    pump_off_v: float = PUMP_OFF_V
    pump_off_i: float = PUMP_OFF_I
    freq_lo: float = FREQ_RANGE[0]
    freq_hi: float = FREQ_RANGE[1]
    freq_gate: bool = True
    transient_window: int = TRANSIENT_WINDOW
    transient_min_periods: int = TRANSIENT_MIN_PERIODS
    transient_cv: float = TRANSIENT_CV
    transient_gate: bool = True
    elec_basis_lo: float = ELEC_BASIS_LO
    elec_basis_hi: float = ELEC_BASIS_HI

    @property
    def is_default(self) -> bool:
        return self == DEFAULT_THRESHOLDS

    @property
    def key(self) -> str:
        """Short stable id, used in cache keys and file names. Empty when nothing was changed."""
        if self.is_default:
            return ""
        raw = repr(tuple(getattr(self, f.name) for f in fields(self))).encode()
        return hashlib.md5(raw).hexdigest()[:8]

    def changes(self) -> list[tuple[str, str, str]]:
        """(label, default, now) for every rule that differs from the default."""
        out = []
        for f in fields(self):
            now, default = getattr(self, f.name), getattr(DEFAULT_THRESHOLDS, f.name)
            if now != default:
                out.append((RULE_LABELS.get(f.name, f.name), _rule_str(f.name, default),
                            _rule_str(f.name, now)))
        return out


RULE_LABELS = {
    "min_dp": "dP floor", "pip_lo": "PIP minimum", "pip_hi": "PIP maximum",
    "pdp_max": "PDP ceiling",
    "whp_over_pdp": "Reject WHP > PDP", "pump_off_v": "Pump off below voltage",
    "pump_off_i": "Pump off below current", "freq_lo": "Frequency minimum",
    "freq_hi": "Frequency maximum", "freq_gate": "Apply the frequency rule",
    "transient_window": "Transient window (samples)",
    "transient_min_periods": "Transient minimum samples",
    "transient_cv": "Transient CV limit", "transient_gate": "Apply the transient rule",
    "elec_basis_lo": "Calibrated voltage window, low",
    "elec_basis_hi": "Calibrated voltage window, high",
}


def _rule_str(name: str, v) -> str:
    if isinstance(v, bool):
        return "on" if v else "off"
    if name == "min_dp" and not v:
        return "off (dP > 0 only)"
    if name == "pdp_max":
        return "off (no ceiling)" if not v else f"{v:g}"
    if name == "transient_cv":
        return f"{v * 100:g} %"
    if name in ("elec_basis_lo", "elec_basis_hi"):
        return f"x {v:g}"
    return f"{v:g}"


DEFAULT_THRESHOLDS = Thresholds()

# --- mapping (spec section 3) ---
MAP_WINDOW_H = 12
MAP_WINDOW_WIDE_H = 24
MAP_MIN_ROWS = 6

# --- calibration (spec section 4) ---
ROBUST_Z_MAX = 3.5
MAD_SCALE = 1.4826

# --- diagnosis (spec section 7) ---
GAP_DAYS = 2
PUMP_OFF_HOURS = 6
VOLT_CHANGE_PCT = 30
RATE_STEP_PCT = 15
RATE_STEP_MIN_DAYS = 3
RATE_STEP_TRAIL_DAYS = 7
PHI_BAND = (0.95, 1.05)
PHI_DRIFT_MIN_DAYS = 7
PHI_ROLL_DAYS = 7
BACKPRESSURE_FACTOR = 1.5
BACKPRESSURE_TRAIL_DAYS = 30
BACKPRESSURE_MIN_DAYS = 2
LOW_PIP_PCT = 15
LOW_PIP_ROLL_DAYS = 30

# Camilleri constant: 58847 * 746 / sqrt(3) ... used only for the efficiency caveat
# implied overall efficiency = K * 1000 / 78818
EFF_DENOM = 78818.0

# --- third read-only input: pump-run metadata (ruling 3) ---
ESP_MASTER_FILE = DATA_DIR / "ESP_MASTER_DATASET.csv"

# --- voltage tiers (ruling 2) ---
VOLT_STEP_PCT = 10            # 10-30 % daily-median change -> VOLTAGE_STEP (info)

# --- PHI wording (ruling 5): use verbatim wherever PHI is displayed ---
PHI_HELP = ("Ratio of dP/(sqrt(3)*V*I) to its value at calibration. It moves when the operating point "
            "changes as well as when the pump degrades. PHI ~ 1 means nothing has changed since "
            "calibration; a sustained drift means recalibrate or investigate.")

# --- PVT / water cut (spec addendum A and B) ---------------------------------
PVT_FILE = DATA_DIR / "pvtdetails_4wells.csv"   # per-well lab PVT: Pb, Rs, Bo, viscosity, API, T
BW = 1.020                 # water formation volume factor, rb/stb (implied by ESP_MASTER_DATASET)
WATER_SG = 1.0958          # Mauddud formation water, 137,779 ppm
GAS_GRAVITY = 0.80         # ASSUMED: not present in any input file -> GVF is indicative only
Z_FACTOR = 0.9             # assumed gas compressibility for the intake Bg
GVF_CAVEAT = "estimated (gas gravity assumed 0.80)"

# GAS_AT_INTAKE event and the intake severity bands
GAS_AT_INTAKE_MARGIN = 300     # psi below Pb before the intake counts as gassy
GAS_ROLL_DAYS = 30             # rolling window on the daily PIP for the event

WC_CORRECTION_HELP = (
    "Divides the pump-condition rate by the liquid formation volume factor B_liq(WC, Bo) so K no "
    "longer carries a hidden water-cut dependence. Physically consistent; on this dataset the "
    "effect is within +/-2% because water cut only moves 55-83% in the SCADA period."
)
