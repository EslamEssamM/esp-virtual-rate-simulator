"""Constants shared by the core modules."""
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
CACHE_DIR = APP_DIR / "cache"

RT_FILE = DATA_DIR / "AI_VW_REAL_TIME_DATA_Sample_Date_13-Sep-2026 V 1.1.xlsx"
RT_SHEET = "AI_VW_REAL_TIME_DATA"
WT_FILE = DATA_DIR / "GC31_DIGIWELLS_81_PARAM_MASTER_DATASET.csv"

WELLS = ["SA-0162_T", "SA-0500_T", "SA-0512H_T", "SA-0991H_T"]

# --- quality thresholds (spec section 2) ---
PUMP_OFF_V = 100.0
PUMP_OFF_I = 5.0
MIN_DP = 300.0
PIP_RANGE = (50.0, 5000.0)
PDP_RANGE = (300.0, 6000.0)
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
