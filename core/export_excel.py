"""Excel export: every table the app shows, the calibrated K, and one charted tab per well.

The workbook is built with openpyxl so it is made of *native* Excel objects throughout - Tables
with autofilters, real charts bound to cell ranges, defined names, conditional formats, data
validation and number formats. Nothing is flattened to a picture, so the reader can re-sort,
re-filter, re-plot or paste the numbers straight into their own workbook.

It is also live where that is honest about the method:

  matched tests  ->  K = Q_test / X            (a formula, per test)
  calibration    ->  K single = MEDIAN(K of the well's non-suspect tests)
  well tab       ->  an editable K cell that defaults to that calibration formula
  well tab       ->  Q at K = K_cell * X       (a formula, so its chart series follows the cell)

Blank the K of a test on the "Matched tests" sheet, or type a K into the yellow cell on a well
tab, and the workbook recalculates the way the app's what-if slider does. The app's own numbers
are always exported alongside as values, so the workbook shows both what the app showed and what
the reader's K would give.

Pure Python: no Streamlit in here. `ui/export.py` wraps `workbook_bytes` for the download button.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference, ScatterChart, Series
from openpyxl.chart.axis import DateAxis
from openpyxl.chart.marker import Marker
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.comments import Comment
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter, quote_sheetname
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

from . import config as C
from . import virtual_rate as vr
from .validation import last_test_summary


# --------------------------------------------------------------------------- options

@dataclass
class ExportOptions:
    """Everything the workbook needs that is not in `Results`.

    The display-only maps (well colours, category and method labels, dataset notes) are passed in
    by the app layer rather than imported from `ui/`, so this module stays free of the UI.
    """
    wells: tuple[str, ...] = ()
    start: str = ""
    end: str = ""
    k_mode: str = "interp"              # 'interp' | 'single'
    freq: str = "D"                     # '30min' | 'h' | 'D'
    steady_only: bool = True
    wc_correction: bool = False
    live_formulas: bool = True          # False writes computed values everywhere instead
    per_well_tabs: bool = True
    include_events: bool = True
    include_quality: bool = True
    include_pvt: bool = True
    well_colors: dict = field(default_factory=dict)
    category_labels: dict = field(default_factory=dict)
    method_labels: dict = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    rule_changes: tuple = ()            # (label, default, now) per non-default quality rule

    @property
    def k_name(self) -> str:
        return "K_dh" if self.wc_correction else "K"

    @property
    def k_label(self) -> str:
        return f"{self.k_name} {'interpolated' if self.k_mode == 'interp' else 'single'}"


FREQ_LABEL = {"30min": "30-minute rows", "h": "hourly medians", "D": "daily medians"}
K_MODE_LABEL = {"interp": "Interpolated K", "single": "Single K"}

# Row categories of the data-quality stack, in the order the app draws them.
QUALITY_CATEGORIES = ["steady", "transient", "uncalibrated_basis", "pump_off", "missing_press",
                      "missing_elec", "bad_dP", "bad_press_range", "bad_freq", "gauge_frozen"]
QUALITY_COLORS = ["2A78D6", "86B6EF", "C3C2B7", "52514E", "EB6834",
                  "E87BA4", "EDA100", "E34948", "4A3AA7", "F2A2C0"]


# --------------------------------------------------------------------------- look and feel

INK, INK2, MUTED, RULE = "0B0B0B", "52514E", "898781", "C3C2B7"
ACCENT, EDITABLE = "1F3864", "FFF2C2"
SIGNAL_COLORS = {"PIP": "2A78D6", "PDP": "EB6834", "WHP": "1BAF7A", "Pb": "D03B3B",
                 "VOLTAGE": "4A3AA7", "AMPERAGE": "EDA100", "MT_F": "E87BA4"}
TEST_MARK, SUSPECT_MARK = "0B0B0B", "D03B3B"

TITLE_FONT = Font(name="Calibri", size=16, bold=True, color=INK)
H2_FONT = Font(name="Calibri", size=12, bold=True, color=ACCENT)
CAPTION_FONT = Font(name="Calibri", size=10, italic=True, color=INK2)
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor=ACCENT)
LABEL_FONT = Font(name="Calibri", size=10, color=INK2)
KPI_FONT = Font(name="Calibri", size=14, bold=True, color=INK)
LINK_FONT = Font(name="Calibri", size=11, color="0563C1", underline="single")
THIN = Side(style="thin", color=RULE)
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# Number formats by column name; anything not listed falls back to the dtype default.
FMT = {
    "TIME_STAMP": "yyyy-mm-dd hh:mm", "TEST_TS": "yyyy-mm-dd hh:mm", "BASE_TEST_TS": "yyyy-mm-dd",
    "day": "yyyy-mm-dd", "month": "yyyy-mm", "start": "yyyy-mm-dd hh:mm", "end": "yyyy-mm-dd hh:mm",
    "first": "yyyy-mm-dd", "last": "yyyy-mm-dd", "first_test": "yyyy-mm-dd", "last_test": "yyyy-mm-dd",
    "scada_first": "yyyy-mm-dd", "scada_last": "yyyy-mm-dd", "install_date": "yyyy-mm-dd",
    "run_start": "yyyy-mm-dd", "run_end": "yyyy-mm-dd", "last_test_ts": "yyyy-mm-dd",
    "Q_LIQ": "#,##0", "Q_OIL": "#,##0", "Q_TEST": "#,##0", "Q_single": "#,##0", "Q_interp": "#,##0",
    "Q_M6_single": "#,##0", "Q_M6_interp": "#,##0", "Q_app": "#,##0", "Q_at_K": "#,##0",
    "Q_M1_LOO": "#,##0", "Q_M2_WALK": "#,##0", "Q_M6_LOO": "#,##0", "Q_M6_WALK": "#,##0",
    "Q_BASE_LAST_TEST": "#,##0", "Q_test_used": "#,##0", "Q_test_suspect": "#,##0",
    "last_test_q": "#,##0", "rate_last": "#,##0", "rate_median": "#,##0", "rate_p10": "#,##0",
    "rate_p90": "#,##0", "cum_bbl": "#,##0",
    "K": "0.00", "K_single": "0.00", "K_interp": "0.00", "K_used": "0.00", "K_row": "0.00",
    "K_min": "0.00", "K_max": "0.00", "K_dh": "0.00", "K_dh_single": "0.00", "K_dh_interp": "0.00",
    "K_dh_min": "0.00", "K_dh_max": "0.00", "K_line": "0.00", "K_applied": "0.00",
    "K_dh_used": "0.00",
    "K_cv_pct": "0.0", "K_dh_cv_pct": "0.0", "implied_eff": "0.00",
    "PHI": "0.000", "phi_last": "0.000", "PHI_base": "0.000000", "X": "0.00", "RT_X": "0.00",
    "X_at_test": "0.00",
    "B_LIQ": "0.0000", "B_LIQ_FILE": "0.0000", "BO": "0.0000", "B_LIQ_min": "0.0000",
    "B_LIQ_max": "0.0000", "med_B_LIQ": "0.0000",
    "WC_FRAC": "0.0%", "WC_min": "0.0%", "WC_max": "0.0%", "gvf_est": "0.0%",
    "VOLTAGE": "#,##0", "AMPERAGE": "#,##0.0", "RT_VOLTAGE": "#,##0", "RT_AMPERAGE": "#,##0.0",
    "FREQ_FILLED": "0.0", "FREQUENCY": "0.0", "T_FREQ": "0.0",
    "PIP": "#,##0", "PDP": "#,##0", "WHP": "#,##0", "dP": "#,##0", "Pb": "#,##0",
    "RT_PIP": "#,##0", "RT_PDP": "#,##0", "RT_WHP": "#,##0", "RT_dP": "#,##0", "T_PIP": "#,##0",
    "T_PDP": "#,##0", "T_WHP": "#,##0", "PIP_diff_vs_test": "#,##0", "PIP_minus_Pb": "#,##0",
    "V_lo": "#,##0", "V_hi": "#,##0", "PIP_base": "#,##0", "PIP_base_median": "#,##0",
    "PIP_median": "#,##0", "PIP_minus_Pb_median": "#,##0",
    "MT_F": "#,##0.0", "INTAKE_TEMP_F": "#,##0.0",
    "robust_z": "0.0", "nearest_rt_h": "0.0", "duration_d": "0.00", "change_pct": "+0.0;-0.0",
    "value_before": "#,##0.0", "value_after": "#,##0.0",
    "APE_M1_LOO": "0.0", "APE_M2_WALK": "0.0", "APE_M6_LOO": "0.0", "APE_M6_WALK": "0.0",
    "APE_BASE_LAST_TEST": "0.0", "APE": "0.0", "APE_at_K": "0.0", "last_test_ape": "0.0",
    "MAPE_all": "0.0", "MdAPE_all": "0.0", "MAPE_excl_suspect": "0.0", "MdAPE_excl_suspect": "0.0",
    "MAPE_single": "0.0", "MAPE_interp": "0.0",
    "usable_pct": "0.0", "steady_pct": "0.0", "pct_rows_below_Pb": "0.0", "gvf_median_pct": "0.0",
    "gvf_p95_pct": "0.0", "temp_c_frac": "0.00", "uptime_pct": "0.0", "rate_coverage_pct": "0.0",
    "rows": "#,##0", "n_rows": "#,##0", "scada_rows": "#,##0", "P_elec_kVA": "#,##0",
    "rate_rows": "#,##0", "PB": "#,##0", "RS": "#,##0", "RS_TEST": "#,##0", "Rs_b": "#,##0",
    "OIL_VISCOSITY": "0.00", "oil_visc_cp": "0.00", "API": "0.0", "OIL_FVF": "0.0000",
    "DENSITY": "0.000", "RESERVOIR_TEMP": "#,##0", "med_PIP_minus_Pb": "#,##0",
}

# Human column headers. Anything not listed keeps its pipeline name, which is what the README uses.
LABELS = {
    "WELL_NAME": "Well", "TEST_TS": "Test time", "TIME_STAMP": "Time", "day": "Day", "month": "Month",
    "Q_LIQ": "Q test, BFPD", "Q_TEST": "Q test, BFPD", "Q_OIL": "Q oil, BOPD",
    "Q_app": "Q virtual, BFPD", "Q_at_K": "Q at K cell, BFPD",
    "Q_test_used": "Q test used for K", "Q_test_suspect": "Q suspect test",
    "RT_X": "X at test", "X_at_test": "X at test", "X": "X", "K": "K",
    "K_used": "K used for calibration", "K_dh_used": "K_dh used for calibration",
    "K_row": "K applied by the app", "K_applied": "K applied",
    "K_line": "K cell", "K_single": "K single", "K_interp": "K interpolated",
    "K_cv_pct": "K spread, CV %", "K_dh_single": "K_dh single", "K_dh_cv_pct": "K_dh spread, CV %",
    "implied_eff": "Implied efficiency",
    "n_tests": "Matched tests", "n_suspect": "Suspect tests", "first_test": "First test",
    "last_test": "Last test", "V_BASIS": "V basis", "V_lo": "V window low", "V_hi": "V window high",
    "PHI_base": "PHI baseline", "PIP_base": "PIP baseline, psi",
    "VOLTAGE": "Voltage, V", "AMPERAGE": "Current, A", "FREQ_FILLED": "Frequency, Hz (filled)",
    "FREQUENCY": "Frequency, Hz (raw)", "PIP": "PIP, psi", "PDP": "PDP, psi", "WHP": "WHP, psi",
    "dP": "dP, psi", "MT_F": "Motor temp, degF", "INTAKE_TEMP_F": "Intake temp, degF",
    "RT_VOLTAGE": "V at test", "RT_AMPERAGE": "I at test", "RT_dP": "dP at test, psi",
    "RT_PIP": "PIP SCADA, psi", "T_PIP": "PIP test, psi", "PIP_diff_vs_test": "PIP diff, psi",
    "WC_FRAC": "Water cut", "B_LIQ": "B_liq, rb/stb", "BO": "Bo, rb/stb", "Pb": "Bubble point, psi",
    "PIP_minus_Pb": "PIP - Pb, psi", "gvf_est": "Estimated GVF",
    "robust_z": "Robust z", "suspect": "Suspect", "n_steady": "Steady rows",
    "n_window": "Rows in window", "window_h": "Window, +/- h", "nearest_rt_h": "Nearest SCADA row, h",
    "MATCH": "Match status", "run": "Pump run", "regime": "Regime", "n_rows": "Rows", "rows": "Rows",
    "MAPE_all": "MAPE all, %", "MdAPE_all": "Median APE all, %", "n_all": "n",
    "MAPE_excl_suspect": "MAPE excl. suspect, %", "MdAPE_excl_suspect": "Median APE excl. susp., %",
    "n_excl_suspect": "n excl. suspect", "scope": "Scope", "method": "Method code", "label": "Method",
    "wells_scope": "Wells", "test_rule": "Test set",
    "usable_pct": "Usable %", "steady_pct": "Steady %", "calibrated_basis": "Steady + calibrated basis",
    "temp_unit_c": "degC rows", "excluded": "Excluded", "reason": "Reason", "excluded_by": "Excluded by",
    "scada_rows": "SCADA rows", "well_tests": "Well tests", "pump": "Pump", "manufacturer": "Manufacturer",
    "event_id": "Event", "start": "Start", "end": "End", "type": "Event type", "severity": "Severity",
    "duration_d": "Duration, days", "explanation": "What happened", "evidence": "Evidence",
    "value_before": "Before", "value_after": "After", "change_pct": "Change %", "signal": "Signal",
    "category": "Row category", "install_date": "Install date", "CANONICAL_MODEL": "Pump",
    "NUMBER_OF_STAGES": "Stages", "PUMP_MANUFACTURER": "Manufacturer",
    "INSTALL_TOP_DEPTH_FT": "Install depth, ft", "PB": "Bubble point, psi", "RS": "Rs, scf/stb",
    "Rs_b": "Rs at Pb, scf/stb", "OIL_FVF": "Bo lab, rb/stb", "OIL_VISCOSITY": "Oil viscosity, cp",
    "oil_visc_cp": "Oil viscosity, cp", "DENSITY": "Density", "API": "API",
    "RESERVOIR_TEMP": "Reservoir T, degF", "PIP_median": "PIP median, psi",
    "PIP_minus_Pb_median": "PIP - Pb median, psi", "pct_rows_below_Pb": "Rows below Pb, %",
    "gvf_median_pct": "GVF median, %", "gvf_p95_pct": "GVF p95, %",
}


def label(col: str) -> str:
    return LABELS.get(col, col)


# --------------------------------------------------------------------------- cell plumbing

def _clean(v):
    """Excel-safe value: NaN/NaT -> blank, numpy scalars -> Python, tz-aware -> naive."""
    if v is None:
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (pd.Timestamp, np.datetime64)):
        t = pd.Timestamp(v)
        if pd.isna(t):
            return None
        return t.tz_localize(None).to_pydatetime() if t.tz is not None else t.to_pydatetime()
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if not np.isfinite(f) else f
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):
        # a leading '=' would be read back as a formula
        return v if not v.startswith("=") else "'" + v
    return v


def _fmt_for(col: str, s: pd.Series | None) -> str | None:
    if col in FMT:
        return FMT[col]
    if s is None:
        return None
    if pd.api.types.is_datetime64_any_dtype(s):
        return "yyyy-mm-dd hh:mm"
    if pd.api.types.is_bool_dtype(s):
        return None
    if pd.api.types.is_integer_dtype(s):
        return "#,##0"
    if pd.api.types.is_float_dtype(s):
        return "#,##0.00"
    return None


def _table_name(*parts: str) -> str:
    """Excel table/defined names: letters, digits and underscores only, never a cell reference."""
    n = re.sub(r"[^0-9A-Za-z_]", "_", "_".join(str(p) for p in parts if p))
    if not n or not (n[0].isalpha() or n[0] == "_"):
        n = "T_" + n
    return n[:60]


def _sheet_name(name: str) -> str:
    return re.sub(r"[\[\]:*?/\\]", "-", str(name))[:31]


@dataclass
class Block:
    """A rectangle of written data: its header row, its columns and how to reference them."""
    ws: object
    header_row: int
    first_col: int
    n_rows: int
    cols: list

    @property
    def first_data_row(self) -> int:
        return self.header_row + 1

    @property
    def last_row(self) -> int:
        return self.header_row + max(self.n_rows, 1)

    @property
    def last_col(self) -> int:
        return self.first_col + len(self.cols) - 1

    def has(self, name: str) -> bool:
        return name in self.cols and self.n_rows > 0

    def col(self, name: str) -> int:
        return self.first_col + self.cols.index(name)

    def letter(self, name: str) -> str:
        return get_column_letter(self.col(name))

    def cell(self, name: str, i: int = 0) -> str:
        """Absolute address of row `i` (0-based) of a column, e.g. '$C$12'."""
        return f"${self.letter(name)}${self.first_data_row + i}"

    def rng(self, name: str) -> str:
        return f"${self.letter(name)}${self.first_data_row}:${self.letter(name)}${self.last_row}"

    def abs_rng(self, name: str) -> str:
        return f"{quote_sheetname(self.ws.title)}!{self.rng(name)}"

    def yref(self, name: str) -> Reference:
        c = self.col(name)
        return Reference(self.ws, min_col=c, min_row=self.header_row, max_row=self.last_row)

    def xref(self, name: str) -> Reference:
        c = self.col(name)
        return Reference(self.ws, min_col=c, min_row=self.first_data_row, max_row=self.last_row)


def write_block(ws, df: pd.DataFrame, header_row: int = 1, first_col: int = 1,
                table: str | None = None, style: str = "TableStyleMedium2",
                headers: dict | None = None, widths: dict | None = None,
                formats: dict | None = None, max_width: int = 46) -> Block:
    """Write a frame as a native Excel Table (header + rows) and return its Block."""
    cols = list(df.columns)
    headers = headers or {}
    formats = formats or {}
    # Excel refuses a Table whose header names repeat, so a collision is made unique here rather
    # than producing a workbook that will not open.
    seen, names = {}, []
    for c in cols:
        h = str(headers.get(c, label(c)))
        seen[h] = seen.get(h, 0) + 1
        names.append(h if seen[h] == 1 else f"{h} ({seen[h]})")
    for j, c in enumerate(cols):
        cell = ws.cell(row=header_row, column=first_col + j, value=names[j])
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BOX
    for i, (_, row) in enumerate(df.iterrows(), start=1):
        for j, c in enumerate(cols):
            cell = ws.cell(row=header_row + i, column=first_col + j, value=_clean(row[c]))
            nf = formats.get(c, _fmt_for(c, df[c]))
            if nf:
                cell.number_format = nf
    blk = Block(ws, header_row, first_col, len(df), cols)
    if table and len(df):
        t = Table(displayName=_table_name(table),
                  ref=f"{get_column_letter(first_col)}{header_row}:"
                      f"{get_column_letter(blk.last_col)}{blk.last_row}")
        t.tableStyleInfo = TableStyleInfo(name=style, showRowStripes=True, showColumnStripes=False)
        ws.add_table(t)
    for j, c in enumerate(cols):
        letter = get_column_letter(first_col + j)
        if widths and c in widths:
            w = widths[c]
        else:
            body = df[c].head(200).astype(str).str.len().max()
            w = max(len(names[j]) + 3, int(body) + 2 if pd.notna(body) else 10)
        cur = ws.column_dimensions[letter].width or 0
        ws.column_dimensions[letter].width = min(max(w, cur, 9), max_width)
    ws.row_dimensions[header_row].height = 30
    return blk


def title(ws, text: str, caption: str | None = None, row: int = 1, col: int = 1) -> int:
    ws.cell(row=row, column=col, value=text).font = TITLE_FONT
    if caption:
        c = ws.cell(row=row + 1, column=col, value=caption)
        c.font = CAPTION_FONT
        c.alignment = Alignment(wrap_text=False)
        return row + 3
    return row + 2


def section(ws, text: str, row: int, col: int = 1) -> int:
    ws.cell(row=row, column=col, value=text).font = H2_FONT
    return row + 1


def kv(ws, row: int, col: int, key: str, value, fmt: str | None = None, note: str | None = None):
    k = ws.cell(row=row, column=col, value=key)
    k.font = LABEL_FONT
    v = ws.cell(row=row, column=col + 1, value=_clean(value))
    v.font = Font(name="Calibri", size=11, bold=True, color=INK)
    if fmt:
        v.number_format = fmt
    if note:
        n = ws.cell(row=row, column=col + 2, value=note)
        n.font = CAPTION_FONT
    return row + 1


# --------------------------------------------------------------------------- geometry

def _col_width_cm(ws, col: int) -> float:
    """Approximate on-screen width of a column, so charts can be anchored beside a table."""
    w = ws.column_dimensions[get_column_letter(col)].width
    chars = w if w else 8.43
    return (chars * 7 + 5) * 2.54 / 96.0


def col_after(ws, start_col: int, cm: float) -> int:
    """First column at least `cm` to the right of `start_col`, so nothing overlaps."""
    total, col = 0.0, start_col
    while total < cm and col < 16000:
        total += _col_width_cm(ws, col)
        col += 1
    return col


# --------------------------------------------------------------------------- charts

CHART_W, CHART_H = 16.0, 9.0          # cm; two charts per row fit a laptop screen
CHART_ROWS = 19                        # 19 default-height rows ~ 9 cm


def _series(y: Reference, x: Reference | None = None, color: str | None = None,
            line: bool = True, marker: str | None = None, size: int = 7,
            dash: str | None = None, width: int = 20000) -> Series:
    s = Series(y, x, title_from_data=True)
    if marker:
        s.marker = Marker(symbol=marker, size=size)
        if color:
            s.marker.graphicalProperties = GraphicalProperties(solidFill=color)
            s.marker.graphicalProperties.line.solidFill = color
    else:
        s.marker = Marker(symbol="none")
    if line:
        if color:
            s.graphicalProperties.line.solidFill = color
        s.graphicalProperties.line.width = width
        if dash:
            s.graphicalProperties.line.dashStyle = dash
    else:
        s.graphicalProperties.line.noFill = True
    s.smooth = False
    return s


def time_chart(name: str, y_title: str, series: list, date_fmt: str = "yyyy-mm",
               height: float = CHART_H, width: float = CHART_W) -> ScatterChart:
    """Scatter chart on a real date x-axis: irregular sampling and gaps plot honestly, and
    well tests can be overlaid as markers at their own timestamps."""
    ch = ScatterChart()
    ch.title = name
    ch.style = 2
    ch.height, ch.width = height, width
    ch.y_axis.title = y_title
    ch.x_axis.number_format = date_fmt
    ch.x_axis.majorTickMark = "out"
    ch.y_axis.majorTickMark = "out"
    # openpyxl writes delete=None, which some Excel builds read as "hide the axis"
    ch.x_axis.delete = False
    ch.y_axis.delete = False
    ch.x_axis.majorGridlines = None
    ch.dispBlanksAs = "gap"
    for s in series:
        ch.series.append(s)
    return ch


def _finish(ch, legend_bottom: bool = True):
    if legend_bottom and ch.legend is not None:
        ch.legend.position = "b"
        ch.legend.overlay = False
    return ch


def anchor_charts(ws, charts: list, top_row: int, first_col: int = 1) -> int:
    """Lay charts out two per row from `top_row`; returns the first free row below them."""
    second = col_after(ws, first_col, CHART_W + 0.5)
    row = top_row
    for i, ch in enumerate(charts):
        col = first_col if i % 2 == 0 else second
        ws.add_chart(ch, f"{get_column_letter(col)}{row}")
        if i % 2 == 1:
            row += CHART_ROWS
    if len(charts) % 2 == 1:
        row += CHART_ROWS
    return row + 1


# --------------------------------------------------------------------------- data preparation

SERIES_SIGNALS = ["VOLTAGE", "AMPERAGE", "FREQ_FILLED", "PIP", "PDP", "dP", "WHP", "MT_F"]
SERIES_PVT = ["WC_FRAC", "B_LIQ", "PIP_minus_Pb", "gvf_est", "Pb"]


def k_col(opt: ExportOptions) -> str:
    return ("K_dh_" if opt.wc_correction else "K_") + opt.k_mode


def well_series(res, well: str, opt: ExportOptions) -> pd.DataFrame:
    """The charted series for one well: exactly the rows and resolution the app draws."""
    d = vr.slice_period(res.rt, well, opt.start, opt.end)
    r = vr.rate_rows(d, opt.steady_only)
    q, k = vr.q_col(opt.k_mode, opt.wc_correction), k_col(opt)
    keep = [c for c in [q, k, "PHI", "X"] + SERIES_SIGNALS + SERIES_PVT if c in r.columns]
    if r.empty or not keep:
        return pd.DataFrame(columns=["TIME_STAMP", "Q_app", "PHI", "X", "K_row", "n_rows"])
    if opt.freq == "30min":
        out = r[["TIME_STAMP"] + keep].copy()
        out["n_rows"] = 1
        out = vr.break_gaps(out, pd.Timedelta(hours=2))
    else:
        g = r.set_index("TIME_STAMP")
        out = g[keep].resample(opt.freq).median()
        out["n_rows"] = g["X"].resample(opt.freq).size()
        out = out.reset_index()
    out = out.rename(columns={q: "Q_app", k: "K_row"})
    order = (["TIME_STAMP", "Q_app", "PHI", "X", "K_row"]
             + [c for c in SERIES_SIGNALS + SERIES_PVT if c in out.columns] + ["n_rows"])
    return out[[c for c in order if c in out.columns]].reset_index(drop=True)


def well_test_block(res, well: str, opt: ExportOptions) -> pd.DataFrame:
    """Matched tests of one well, split into a used and a suspect column so a chart can mark
    them differently, plus the columns the K formulas need."""
    m = res.matched
    if not len(m):
        return pd.DataFrame()
    t = m[m["WELL_NAME"] == well].sort_values("TEST_TS")
    lo, hi = pd.Timestamp(opt.start), pd.Timestamp(opt.end) + pd.Timedelta(days=1)
    t = t[(t["TEST_TS"] >= lo) & (t["TEST_TS"] < hi)]
    if not len(t):
        return pd.DataFrame()
    susp = t["suspect"].fillna(False).astype(bool)
    out = pd.DataFrame({
        "TEST_TS": t["TEST_TS"].to_numpy(),
        "Q_LIQ": t["Q_LIQ"].to_numpy(),
        "Q_test_used": t["Q_LIQ"].where(~susp).to_numpy(),
        "Q_test_suspect": t["Q_LIQ"].where(susp).to_numpy(),
        "X_at_test": t["RT_X"].to_numpy(),
        "K": t["K"].to_numpy(),
        "suspect": susp.to_numpy(),
    })
    if opt.wc_correction and "K_dh" in t.columns:
        out["B_LIQ"] = t["B_LIQ"].to_numpy()
        out["K_dh"] = t["K_dh"].to_numpy()
    if "WC_FRAC" in t.columns and t["WC_FRAC"].notna().any():
        out["WC_FRAC"] = t["WC_FRAC"].to_numpy()
        if "B_LIQ" not in out.columns:
            out["B_LIQ"] = t["B_LIQ"].to_numpy()
    out["Q_at_K"] = np.nan
    out["APE_at_K"] = np.nan
    return out.reset_index(drop=True)


def monthly_block(res, well: str, opt: ExportOptions) -> pd.DataFrame:
    """Rows per month by category for one well, wide, in the app's category order."""
    mf = res.monthly_flags
    if not len(mf):
        return pd.DataFrame()
    m0 = pd.Timestamp(opt.start).to_period("M").to_timestamp()
    m1 = pd.Timestamp(opt.end).to_period("M").to_timestamp()
    g = mf[(mf["WELL_NAME"] == well) & (mf["month"] >= m0) & (mf["month"] <= m1)]
    if not len(g):
        return pd.DataFrame()
    piv = g.pivot_table(index="month", columns="category", values="rows", aggfunc="sum", fill_value=0)
    piv = piv[[c for c in QUALITY_CATEGORIES if c in piv.columns]]
    return piv.reset_index()


def in_window(df: pd.DataFrame, col: str, opt: ExportOptions) -> pd.DataFrame:
    if not len(df) or col not in df.columns:
        return df
    lo, hi = pd.Timestamp(opt.start), pd.Timestamp(opt.end) + pd.Timedelta(days=1)
    return df[(df[col] >= lo) & (df[col] < hi)]


def for_wells(df: pd.DataFrame, wells, col: str = "WELL_NAME") -> pd.DataFrame:
    if not len(df) or col not in df.columns:
        return df
    return df[df[col].isin(list(wells))].reset_index(drop=True)


# --------------------------------------------------------------------------- sheets

def readme_sheet(wb: Workbook, res, opt: ExportOptions):
    """Created first so it is the leftmost tab; the index is filled in once the rest exists."""
    ws = wb.active
    ws.title = "Read me"
    ws.sheet_properties.tabColor = ACCENT
    ws.sheet_view.showGridLines = False
    m = res.meta
    r = title(ws, "ESP Virtual Rate - workbook export",
              f"{m['dataset_label']}. Generated {pd.Timestamp.now():%Y-%m-%d %H:%M} from the same "
              f"pipeline code as the app; no number on any sheet is typed in by hand.")
    r = section(ws, "What was exported", r)
    r = kv(ws, r, 1, "Dataset", m["dataset_label"])
    r = kv(ws, r, 1, "Wells", ", ".join(opt.wells) or "(none)")
    r = kv(ws, r, 1, "Period", f"{opt.start} to {opt.end} (both days included)")
    r = kv(ws, r, 1, "K mode", K_MODE_LABEL.get(opt.k_mode, opt.k_mode))
    r = kv(ws, r, 1, "Resolution", FREQ_LABEL.get(opt.freq, opt.freq))
    r = kv(ws, r, 1, "Rows used", "steady rows only" if opt.steady_only
           else "steady and transient rows")
    r = kv(ws, r, 1, "Water-cut correction", "on (Q = K_dh * X / B_liq, model M6)"
           if opt.wc_correction else "off (Q = K * X, model M1)")
    r = kv(ws, r, 1, "Formulas", "live: K and the what-if rate recalculate in Excel"
           if opt.live_formulas else "values only: every cell is a number")
    r = kv(ws, r, 1, "Quality rules", "default"
           if not opt.rule_changes else f"{len(opt.rule_changes)} changed - see below")
    r += 1

    if opt.rule_changes:
        # A workbook built on loosened rules must say so next to its own numbers, or it will be
        # read as the published result.
        r = section(ws, "Quality rules changed from the default", r)
        c = ws.cell(row=r, column=1, value="Every number in this workbook was computed under these "
                                           "rules, not the app's defaults.")
        c.font = Font(name="Calibri", size=11, bold=True, color=SUSPECT_MARK)
        r += 1
        rules = pd.DataFrame(list(opt.rule_changes), columns=["rule", "default", "in this export"])
        b = write_block(ws, rules, header_row=r, table="Quality_rules",
                        headers={"rule": "Rule", "default": "Default", "in this export": "In this export"})
        r = b.last_row + 2

    r = section(ws, "The method, in three lines", r)
    for line in ["X  =  SQRT(3) * VOLTAGE * AMPERAGE / (PDP - PIP)",
                 "Q_virtual  =  K * X          with K = Q_test / X at each matched well test",
                 "PHI  =  [dP / (SQRT(3) * V * I)]  /  the same ratio at calibration"]:
        c = ws.cell(row=r, column=1, value=line)
        c.font = Font(name="Consolas", size=11, color=INK)
        r += 1
    r += 1

    r = section(ws, "How to change K and see the effect", r)
    for line in [
        "1.  Open a well tab. The yellow cell near the top is that well's calibration factor.",
        "2.  Type a different K into it. The 'Q at K cell' column, its chart series and the "
        "per-test error all follow it. Delete your value to go back to the calibrated formula.",
        "3.  On 'Matched tests', blanking the 'K used for calibration' cell of a test drops that "
        "test from the median, which is what excluding a suspect test does in the app.",
        "4.  The 'Q virtual' column always holds the app's own numbers and never moves.",
    ]:
        c = ws.cell(row=r, column=1, value=line)
        c.font = LABEL_FONT
        c.alignment = Alignment(wrap_text=False)
        r += 1
    r += 1
    if opt.notes:
        r = section(ws, "Known dataset facts", r)
        for n in opt.notes:
            c = ws.cell(row=r, column=1, value="- " + str(n))
            c.font = LABEL_FONT
            r += 1
        r += 1
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 40
    return ws, r


def index_on_readme(ws, start_row: int, entries: list):
    """Hyperlinked table of contents, written once every other sheet exists."""
    r = section(ws, "Sheets in this workbook", start_row)
    for name, desc in entries:
        c = ws.cell(row=r, column=1, value=name)
        c.hyperlink = f"#{quote_sheetname(name)}!A1"
        c.font = LINK_FONT
        d = ws.cell(row=r, column=2, value=desc)
        d.font = LABEL_FONT
        r += 1
    return r


def summary_sheet(wb: Workbook, res, opt: ExportOptions, stats: dict, cal_by_well: dict) -> str:
    """One row per well: the Overview tiles, side by side, plus two comparison charts."""
    ws = wb.create_sheet(_sheet_name("Wells overview"))
    ws.sheet_view.showGridLines = False
    r = title(ws, "Wells overview",
              "Every tile of the Overview page, one row per well, over the exported period.")
    last = last_test_summary(res.validation, res.matched)
    last = last.set_index("WELL_NAME") if len(last) else last
    mape = res.mape.set_index(["scope", "method"]) if len(res.mape) else res.mape
    runs = res.runs.set_index("WELL_NAME") if len(res.runs) else res.runs
    excluded = dict(zip(res.excluded["WELL_NAME"], res.excluded["reason"])) if len(res.excluded) else {}
    m_single = "M6_LOO" if opt.wc_correction else "M1_LOO"
    m_interp = "M6_WALK" if opt.wc_correction else "M2_WALK"

    rows = []
    for w in opt.wells:
        s = stats.get(w, {})
        cr = cal_by_well.get(w)
        run = runs.loc[w].to_dict() if len(runs) and w in runs.index else {}
        lt = last.loc[w] if len(last) and w in last.index else None

        def mp(method):
            return float(mape.loc[(w, method), "MAPE_excl_suspect"]) \
                if len(mape) and (w, method) in mape.index else np.nan
        rows.append({
            "WELL_NAME": w,
            "status": "excluded" if w in excluded else "analysed",
            "CANONICAL_MODEL": run.get("CANONICAL_MODEL", ""),
            "NUMBER_OF_STAGES": run.get("NUMBER_OF_STAGES", np.nan),
            "install_date": run.get("install_date", pd.NaT),
            "rows": s.get("rows", 0), "rate_rows": s.get("rate_rows", 0),
            "rate_last": s.get("rate_last", np.nan), "rate_median": s.get("rate_median", np.nan),
            "rate_p10": s.get("rate_p10", np.nan), "rate_p90": s.get("rate_p90", np.nan),
            "cum_bbl": s.get("cum_bbl", np.nan),
            "K_applied": s.get("K_dh" if opt.wc_correction else "K", np.nan),
            "K_single": float(cr["K_dh_single" if opt.wc_correction else "K_single"]) if cr is not None else np.nan,
            "K_cv_pct": float(cr["K_dh_cv_pct" if opt.wc_correction else "K_cv_pct"]) if cr is not None else np.nan,
            "n_tests": int(cr["n_tests"]) if cr is not None else 0,
            "MAPE_single": mp(m_single), "MAPE_interp": mp(m_interp),
            "last_test_q": float(lt["last_test_q"]) if lt is not None else np.nan,
            "last_test_ts": lt["last_test_ts"] if lt is not None else pd.NaT,
            "last_test_ape": float(lt["last_test_ape"]) if lt is not None else np.nan,
            "phi_last": s.get("phi_last", np.nan),
            "med_PIP_minus_Pb": s.get("med_PIP_minus_Pb", np.nan),
            "uptime_pct": s.get("uptime_pct", np.nan), "steady_pct": s.get("steady_pct", np.nan),
            "rate_coverage_pct": s.get("rate_coverage_pct", np.nan),
        })
    df = pd.DataFrame(rows)
    heads = {"status": "Status", "rate_last": "Q virtual last, BFPD", "rate_median": "Q median, BFPD",
             "rate_p10": "Q p10, BFPD", "rate_p90": "Q p90, BFPD", "cum_bbl": "Cumulative liquid, bbl",
             "rate_rows": "Rows with a rate", "K_applied": f"{opt.k_label} (last row)",
             "MAPE_single": "MAPE single K, %", "MAPE_interp": "MAPE interpolated K, %",
             "last_test_q": "Last test, BFPD", "last_test_ts": "Last test date",
             "last_test_ape": "Last test error, %", "phi_last": "PHI last",
             "med_PIP_minus_Pb": "Intake - Pb, psi", "uptime_pct": "Uptime %",
             "rate_coverage_pct": "Rate coverage %"}
    blk = write_block(ws, df, header_row=r, table="Wells_overview", headers=heads)
    ws.freeze_panes = ws.cell(row=blk.first_data_row, column=blk.first_col + 1)

    if len(df):
        for c in ("MAPE_single", "MAPE_interp"):
            ws.conditional_formatting.add(
                blk.rng(c).replace("$", ""),
                ColorScaleRule(start_type="min", start_color="C6EFCE",
                               mid_type="percentile", mid_value=50, mid_color="FFEB9C",
                               end_type="max", end_color="FFC7CE"))
        ws.conditional_formatting.add(blk.rng("steady_pct").replace("$", ""),
                                      DataBarRule(start_type="num", start_value=0,
                                                  end_type="num", end_value=100, color="2A78D6"))
        cats = blk.xref("WELL_NAME")
        ch1 = BarChart()
        ch1.type, ch1.grouping, ch1.title = "col", "clustered", "Virtual rate per well, BFPD"
        ch1.height, ch1.width = CHART_H, CHART_W
        ch1.add_data(Reference(ws, min_col=blk.col("rate_median"), max_col=blk.col("rate_last"),
                               min_row=blk.header_row, max_row=blk.last_row), titles_from_data=True)
        ch1.set_categories(cats)
        ch1.y_axis.title = "BFPD"
        ch2 = BarChart()
        ch2.type, ch2.grouping, ch2.title = "col", "clustered", "Model error per well (MAPE excl. suspect, %)"
        ch2.height, ch2.width = CHART_H, CHART_W
        ch2.add_data(Reference(ws, min_col=blk.col("MAPE_single"), max_col=blk.col("MAPE_interp"),
                               min_row=blk.header_row, max_row=blk.last_row), titles_from_data=True)
        ch2.set_categories(cats)
        ch2.y_axis.title = "%"
        anchor_charts(ws, [_finish(ch1), _finish(ch2)], top_row=blk.last_row + 3)
    return ws.title


def matched_sheet(wb: Workbook, res, opt: ExportOptions):
    """Matched tests with live K formulas. Returns (sheet, block, per-(well, regime) row ranges)."""
    ws = wb.create_sheet(_sheet_name("Matched tests"))
    ws.sheet_view.showGridLines = False
    r = title(ws, "Matched well tests",
              "K = Q test / X at every well test with steady SCADA rows within +/-12 h (widened to "
              "+/-24 h when needed). Suspect tests (robust z > 3.5 within the well) are kept and "
              "shown, but their 'K used for calibration' cell is blank, so they do not enter the median.")
    mm = for_wells(res.matched, opt.wells)
    has_dh = len(mm) and "K_dh" in mm.columns and mm["K_dh"].notna().any()
    if len(mm):
        mm = mm.sort_values(["WELL_NAME", "regime", "TEST_TS"]).reset_index(drop=True)
        susp = mm["suspect"].fillna(False).astype(bool)
        # a suspect test keeps its K on screen but contributes a blank to the median below
        mm["K_used"] = mm["K"].where(~susp)
        if has_dh:
            mm["K_dh_used"] = mm["K_dh"].where(~susp)
    cols = [c for c in ["WELL_NAME", "regime", "TEST_TS", "Q_LIQ", "RT_X", "K", "K_used",
                        "WC_FRAC", "B_LIQ", "K_dh", "K_dh_used", "PIP_diff_vs_test", "robust_z",
                        "suspect", "n_steady", "window_h", "V_BASIS", "RT_VOLTAGE", "RT_AMPERAGE",
                        "RT_dP", "T_PIP", "RT_PIP", "RT_PDP", "RT_WHP", "run"]
            if c in mm.columns]
    df = mm[cols] if len(mm) else pd.DataFrame(columns=cols)
    blk = write_block(ws, df, header_row=r, table="Matched_tests")
    ws.freeze_panes = ws.cell(row=blk.first_data_row, column=blk.first_col + 1)

    if opt.live_formulas and len(df):
        for i in range(len(df)):
            row = blk.first_data_row + i
            q, x = f"{blk.letter('Q_LIQ')}{row}", f"{blk.letter('RT_X')}{row}"
            ws.cell(row=row, column=blk.col("K"), value=f"=IFERROR({q}/{x},\"\")")
            ws.cell(row=row, column=blk.col("K_used"),
                    value=f"=IF({blk.letter('suspect')}{row},\"\",{blk.letter('K')}{row})")
            if "K_dh" in blk.cols and "B_LIQ" in blk.cols:
                ws.cell(row=row, column=blk.col("K_dh"),
                        value=f"=IFERROR({blk.letter('K')}{row}*{blk.letter('B_LIQ')}{row},\"\")")
                ws.cell(row=row, column=blk.col("K_dh_used"),
                        value=f"=IF({blk.letter('suspect')}{row},\"\",{blk.letter('K_dh')}{row})")
            for c in ("K", "K_used", "K_dh", "K_dh_used"):
                if c in blk.cols:
                    ws.cell(row=row, column=blk.col(c)).number_format = FMT[c]
    ranges = {}
    if len(df):
        for (w, reg), g in df.groupby(["WELL_NAME", "regime"], sort=False):
            pos = [df.index.get_loc(i) for i in g.index]
            ranges[(w, int(reg))] = (blk.first_data_row + min(pos), blk.first_data_row + max(pos))
    return ws, blk, ranges


def calibration_sheet(wb: Workbook, res, opt: ExportOptions, matched_ws, matched_blk,
                      ranges) -> tuple[dict, str]:
    """The calibration table. With live formulas K single is a MEDIAN over the well's block on
    the Matched tests sheet, so dropping a test there re-calibrates the whole workbook.

    Returns ({well: absolute address of the K cell the chosen mode uses}, sheet title).
    """
    ws = wb.create_sheet(_sheet_name("Calibration K"))
    ws.sheet_view.showGridLines = False
    r = title(ws, "Calibration factor K per well",
              "One row per well and electrical regime. K lumps power factor, motor and pump "
              "efficiency, transformer and cable ratios and the downhole-to-surface volume factor.")
    cal = for_wells(res.cal, opt.wells)
    has_dh = len(cal) and "K_dh_single" in cal.columns and cal["K_dh_single"].notna().any()
    cols = ["WELL_NAME", "regime", "K_single", "K_min", "K_max", "K_cv_pct"]
    if has_dh:
        cols += ["K_dh_single", "K_dh_cv_pct"]
    cols += ["n_tests", "n_suspect", "first_test", "last_test", "V_BASIS", "V_lo", "V_hi",
             "PHI_base", "PIP_base", "implied_eff"]
    cols = [c for c in cols if c in cal.columns]
    cal = cal.sort_values(["WELL_NAME", "regime"]).reset_index(drop=True) if len(cal) else cal
    df = cal[cols] if len(cal) else pd.DataFrame(columns=cols)
    blk = write_block(ws, df, header_row=r, table="Calibration_K")
    ws.freeze_panes = ws.cell(row=blk.first_data_row, column=blk.first_col + 1)

    def block_ref(key, col):
        lo, hi = ranges[key]
        letter = matched_blk.letter(col)
        return f"{quote_sheetname(matched_ws.title)}!${letter}${lo}:${letter}${hi}"

    if opt.live_formulas and len(df):
        for i in range(len(df)):
            row = blk.first_data_row + i
            key = (df["WELL_NAME"].iloc[i], int(df["regime"].iloc[i]))
            if key not in ranges:
                continue
            ku = block_ref(key, "K_used")
            live = {"K_single": f"=IFERROR(MEDIAN({ku}),\"\")",
                    "K_min": f"=IFERROR(MIN({ku}),\"\")",
                    "K_max": f"=IFERROR(MAX({ku}),\"\")",
                    "K_cv_pct": f"=IFERROR(STDEV({ku})/AVERAGE({ku})*100,\"\")",
                    "n_tests": f"=COUNT({ku})"}
            if has_dh and "K_dh_used" in matched_blk.cols:
                kd = block_ref(key, "K_dh_used")
                live["K_dh_single"] = f"=IFERROR(MEDIAN({kd}),\"\")"
                live["K_dh_cv_pct"] = f"=IFERROR(STDEV({kd})/AVERAGE({kd})*100,\"\")"
            if "implied_eff" in blk.cols:
                live["implied_eff"] = f"={blk.letter('K_single')}{row}*1000/{C.EFF_DENOM:.0f}"
            for c, f in live.items():
                if c in blk.cols:
                    cell = ws.cell(row=row, column=blk.col(c), value=f)
                    if FMT.get(c):
                        cell.number_format = FMT[c]
    r = blk.last_row + 2
    for line in ["K single is the median K of the well's matched, non-suspect tests.",
                 "K interpolated is linear in time between those tests and flat outside them; it "
                 "is a row-level value, so it is exported on the well tabs rather than here.",
                 f"Implied efficiency = K x 1000 / {C.EFF_DENOM:.0f}: the product PF x eta_m x eta_p "
                 "that K implies. It is reported as a caveat, never used to correct anything."]:
        ws.cell(row=r, column=1, value=line).font = CAPTION_FONT
        r += 1

    # the well tabs point at the latest regime of each well, like the app's own K tile
    k_field = "K_dh_single" if (opt.wc_correction and has_dh) else "K_single"
    refs = {}
    if len(df) and k_field in blk.cols:
        for i in range(len(df)):
            refs[df["WELL_NAME"].iloc[i]] = (
                f"{quote_sheetname(ws.title)}!{blk.cell(k_field, i)}",
                float(cal[k_field].iloc[i]) if pd.notna(cal[k_field].iloc[i]) else np.nan)
    return refs, ws.title


def tests_sheet(wb: Workbook, res, opt: ExportOptions) -> str:
    ws = wb.create_sheet(_sheet_name("All well tests"))
    ws.sheet_view.showGridLines = False
    t = for_wells(res.mapped, opt.wells)
    counts = t["MATCH"].value_counts().to_dict() if len(t) else {}
    r = title(ws, "All well tests and their match status",
              "Every test loaded for the selected wells. "
              + ", ".join(f"{k}: {v}" for k, v in counts.items())
              + ". Unmatched tests have no steady SCADA rows in the matching window.")
    cols = [c for c in ["WELL_NAME", "TEST_TS", "Q_LIQ", "Q_OIL", "WC_FRAC", "MATCH", "n_window",
                        "n_steady", "nearest_rt_h", "window_h", "regime", "run", "T_PIP", "T_PDP",
                        "T_WHP", "T_FREQ", "RT_X", "RT_VOLTAGE", "RT_AMPERAGE", "RT_dP", "B_LIQ"]
            if c in t.columns]
    df = t[cols].sort_values(["WELL_NAME", "TEST_TS"]) if len(t) else pd.DataFrame(columns=cols)
    blk = write_block(ws, df, header_row=r, table="All_well_tests")
    ws.freeze_panes = ws.cell(row=blk.first_data_row, column=blk.first_col + 1)
    return ws.title


def validation_sheet(wb: Workbook, res, opt: ExportOptions) -> str:
    ws = wb.create_sheet(_sheet_name("Validation"))
    ws.sheet_view.showGridLines = False
    r = title(ws, "Validation against the well tests",
              "One row per matched test. M1 = single K from the well's other non-suspect tests "
              "(leave-one-out), M2 = K from the most recent earlier non-suspect test "
              "(walk-forward), baseline = the last well-test rate carried forward. "
              "M6 is the water-cut corrected twin of M1 and M2.")
    v = for_wells(res.validation, opt.wells)
    methods = [m for m in ["M1_LOO", "M2_WALK", "M6_LOO", "M6_WALK", "BASE_LAST_TEST"]
               if f"Q_{m}" in v.columns]
    cols = (["WELL_NAME", "regime", "TEST_TS", "Q_TEST", "X", "K", "suspect"]
            + [f"Q_{m}" for m in methods] + [f"APE_{m}" for m in methods])
    cols = [c for c in cols if c in v.columns]
    df = v[cols] if len(v) else pd.DataFrame(columns=cols)
    heads = {f"APE_{m}": f"APE {opt.method_labels.get(m, m)}, %" for m in methods}
    heads.update({f"Q_{m}": f"Q {opt.method_labels.get(m, m)}, BFPD" for m in methods})
    blk = write_block(ws, df, header_row=r, table="Validation", headers=heads)
    ws.freeze_panes = ws.cell(row=blk.first_data_row, column=blk.first_col + 1)
    if opt.live_formulas and len(df):
        for i in range(len(df)):
            row = blk.first_data_row + i
            q = f"{blk.letter('Q_TEST')}{row}"
            for m in methods:
                p = f"{blk.letter('Q_' + m)}{row}"
                cell = ws.cell(row=row, column=blk.col(f"APE_{m}"),
                               value=f"=IFERROR(ABS({p}-{q})/{q}*100,\"\")")
                cell.number_format = "0.0"
    if len(df):
        for m in methods:
            ws.conditional_formatting.add(
                blk.rng(f"APE_{m}").replace("$", ""),
                ColorScaleRule(start_type="num", start_value=0, start_color="C6EFCE",
                               mid_type="num", mid_value=10, mid_color="FFEB9C",
                               end_type="num", end_value=30, end_color="FFC7CE"))
    return ws.title


def errors_sheet(wb: Workbook, res, opt: ExportOptions) -> str:
    ws = wb.create_sheet(_sheet_name("Error by method"))
    ws.sheet_view.showGridLines = False
    r = title(ws, "Error by method",
              "Mean and median absolute percentage error over the matched tests that every method "
              "can be scored on (those with a leave-one-out value).")
    mo = res.mape_overall.copy()
    if len(mo):
        mo["label"] = mo["method"].map(lambda m: opt.method_labels.get(m, m))
    cols = ["label", "method", "MAPE_all", "MdAPE_all", "n_all", "MAPE_excl_suspect",
            "MdAPE_excl_suspect", "n_excl_suspect"]
    r = section(ws, "All analysed wells", r)
    b1 = write_block(ws, mo[cols] if len(mo) else pd.DataFrame(columns=cols),
                     header_row=r, table="Error_overall")
    if len(mo):
        ch = BarChart()
        ch.type, ch.grouping, ch.title = "col", "clustered", "MAPE by method, %"
        ch.height, ch.width = CHART_H, CHART_W
        ch.add_data(Reference(ws, min_col=b1.col("MAPE_all"), max_col=b1.col("MAPE_all"),
                              min_row=b1.header_row, max_row=b1.last_row), titles_from_data=True)
        ch.add_data(Reference(ws, min_col=b1.col("MAPE_excl_suspect"), max_col=b1.col("MAPE_excl_suspect"),
                              min_row=b1.header_row, max_row=b1.last_row), titles_from_data=True)
        ch.set_categories(b1.xref("label"))
        ch.y_axis.title = "%"
        ws.add_chart(_finish(ch), f"{get_column_letter(b1.last_col + 2)}{b1.header_row}")

    r = b1.last_row + 3
    r = section(ws, "Per well", r)
    mw = res.mape[(res.mape["scope"] != "ALL") & res.mape["scope"].isin(opt.wells)].copy() \
        if len(res.mape) else res.mape.copy()
    if len(mw):
        mw["label"] = mw["method"].map(lambda m: opt.method_labels.get(m, m))
    wcols = ["scope"] + cols
    b2 = write_block(ws, mw[wcols] if len(mw) else pd.DataFrame(columns=wcols),
                     header_row=r, table="Error_per_well")

    r = b2.last_row + 3
    r = section(ws, "Effect of the exclusion and test-set rules", r)
    sv = res.sensitivity.copy()
    if len(sv):
        sv["label"] = sv["method"].map(lambda m: opt.method_labels.get(m, m))
    scols = ["wells_scope", "test_rule"] + cols
    write_block(ws, sv[scols] if len(sv) else pd.DataFrame(columns=scols),
                header_row=r, table="Error_sensitivity")
    return ws.title


def quality_sheet(wb: Workbook, res, opt: ExportOptions) -> str:
    ws = wb.create_sheet(_sheet_name("Data quality"))
    ws.sheet_view.showGridLines = False
    r = title(ws, "Data quality",
              "Counts over the full SCADA history per well. Flags overlap (a row can be pump_off "
              "and bad_dP), so the flag columns do not sum to the row count.")
    fs = for_wells(res.filter_summary, opt.wells)
    cols = [c for c in ["WELL_NAME", "excluded", "rows", "usable", "usable_pct", "steady",
                        "steady_pct", "calibrated_basis", "missing_elec", "missing_press",
                        "pump_off", "bad_dP", "bad_press_range", "bad_freq", "transient",
                        "gauge_frozen", "temp_unit_c", "first", "last"] if c in fs.columns]
    blk = write_block(ws, fs[cols] if len(fs) else pd.DataFrame(columns=cols),
                      header_row=r, table="Filter_summary")
    if len(fs):
        for c in ("usable_pct", "steady_pct"):
            if c in blk.cols:
                ws.conditional_formatting.add(
                    blk.rng(c).replace("$", ""),
                    DataBarRule(start_type="num", start_value=0, end_type="num", end_value=100,
                                color="2A78D6"))
    r = blk.last_row + 2
    for line in ["usable = none of missing_elec, missing_press, pump_off, bad_dP, bad_press_range, bad_freq",
                 "steady = usable and not transient (rolling 6-sample CV of current or dP <= 5 %)",
                 "calibrated basis = steady and VOLTAGE inside 0.8 x min ... 1.2 x max of the "
                 "voltages at the well's calibration tests. Only these rows carry a rate."]:
        ws.cell(row=r, column=1, value=line).font = CAPTION_FONT
        r += 1

    r += 2
    r = section(ws, "Rows per month by category", r)
    mf = for_wells(res.monthly_flags, opt.wells)
    mf = mf[(mf["month"] >= pd.Timestamp(opt.start).to_period("M").to_timestamp())
            & (mf["month"] <= pd.Timestamp(opt.end).to_period("M").to_timestamp())] if len(mf) else mf
    if len(mf):
        mf = mf.copy()
        mf["category"] = mf["category"].map(lambda c: opt.category_labels.get(c, c))
    write_block(ws, mf if len(mf) else pd.DataFrame(columns=["WELL_NAME", "month", "category", "rows"]),
                header_row=r, table="Monthly_categories", style="TableStyleMedium9")
    return ws.title


def events_sheet(wb: Workbook, res, opt: ExportOptions) -> str:
    ws = wb.create_sheet(_sheet_name("Events"))
    ws.sheet_view.showGridLines = False
    r = title(ws, "Diagnosis events",
              "Every event the pipeline detected that overlaps the exported period, with the "
              "evidence behind it. Filter the 'Event type' or 'Severity' column to work through them.")
    e = res.events
    if len(e):
        lo, hi = pd.Timestamp(opt.start), pd.Timestamp(opt.end) + pd.Timedelta(days=1)
        e = e[e["WELL_NAME"].isin(list(opt.wells)) & (e["end"] >= lo) & (e["start"] < hi)]
    cols = list(e.columns) if len(e) else ["event_id", "WELL_NAME", "start", "end", "type",
                                           "severity", "duration_d", "explanation", "evidence"]
    blk = write_block(ws, e[cols] if len(e) else pd.DataFrame(columns=cols), header_row=r,
                      table="Events", widths={"explanation": 60, "evidence": 46}, max_width=60)
    ws.freeze_panes = ws.cell(row=blk.first_data_row, column=blk.first_col + 1)
    return ws.title


def pvt_sheet(wb: Workbook, res, opt: ExportOptions) -> str:
    ws = wb.create_sheet(_sheet_name("PVT and gas"))
    ws.sheet_view.showGridLines = False
    r = title(ws, "PVT, water cut and free gas at the intake",
              "The power equation returns the rate at pump conditions; the well tests are measured "
              "at surface. B_liq is the ratio between the two.")
    r = section(ws, "Laboratory PVT", r)
    lab = for_wells(res.lab_pvt, opt.wells)
    b = write_block(ws, lab if len(lab) else pd.DataFrame(columns=["WELL_NAME"]),
                    header_row=r, table="Lab_PVT")
    r = b.last_row + 3
    r = section(ws, "Intake pressure against the bubble point", r)
    gas = for_wells(res.gas, opt.wells)
    b = write_block(ws, gas if len(gas) else pd.DataFrame(columns=["WELL_NAME"]),
                    header_row=r, table="Gas_at_intake")
    ws.cell(row=b.last_row + 1, column=1,
            value=f"The gas fraction is {C.GVF_CAVEAT} and is indicative, not quantitative.").font = CAPTION_FONT
    r = b.last_row + 4
    r = section(ws, "Water cut and B_liq at each well test", r)
    tp = for_wells(res.test_pvt, opt.wells)
    b = write_block(ws, tp if len(tp) else pd.DataFrame(columns=["WELL_NAME"]),
                    header_row=r, table="Test_PVT")
    if len(tp):
        ws.conditional_formatting.add(
            b.rng("WC_FRAC").replace("$", ""),
            DataBarRule(start_type="num", start_value=0, end_type="num", end_value=1, color="2A78D6"))
    return ws.title


def reference_sheet(wb: Workbook, res, opt: ExportOptions) -> str:
    """Pump metadata, regimes and excluded wells: the static facts behind the well tabs."""
    ws = wb.create_sheet(_sheet_name("Wells and pumps"))
    ws.sheet_view.showGridLines = False
    r = title(ws, "Wells, pumps and regimes",
              "Static data per well, the electrical regimes the pipeline found, and every well "
              "that is loaded but kept out of the analysis, with the reason.")
    r = section(ws, "Pump runs", r)
    b = write_block(ws, for_wells(res.runs, opt.wells), header_row=r, table="Pump_runs")
    r = b.last_row + 3
    r = section(ws, "Electrical regimes", r)
    b = write_block(ws, for_wells(res.regimes, opt.wells), header_row=r, table="Regimes")
    ws.cell(row=b.last_row + 1, column=1,
            value="A well whose transformer ratio or stage count changed mid-life is split into "
                  "regimes and calibrated separately.").font = CAPTION_FONT
    r = b.last_row + 4
    r = section(ws, "Excluded wells", r)
    exc = res.excluded
    b = write_block(ws, exc if len(exc) else pd.DataFrame(columns=["WELL_NAME", "reason"]),
                    header_row=r, table="Excluded_wells", widths={"reason": 70}, max_width=70)
    return ws.title


# --------------------------------------------------------------------------- per-well tab

CHART_TOP = 10           # first row of the chart band
DATA_ROW = 88            # first row of the series table: below four rows of charts
K_ROW = 7                # the editable calibration cell


def well_sheet(wb: Workbook, res, opt: ExportOptions, well: str, k_ref, stats: dict) -> str:
    """One tab per well: the Overview tiles, an editable K, every chart the app draws for that
    well, and the series behind them."""
    ws = wb.create_sheet(_sheet_name(well))
    ws.sheet_view.showGridLines = False
    color = str(opt.well_colors.get(well, "#2A78D6")).lstrip("#").upper()
    ws.sheet_properties.tabColor = color
    runs = res.runs.set_index("WELL_NAME") if len(res.runs) else res.runs
    run = runs.loc[well].to_dict() if len(runs) and well in runs.index else {}
    pump = str(run.get("CANONICAL_MODEL", "") or "")
    stages = run.get("NUMBER_OF_STAGES")
    caption = " | ".join(x for x in [
        f"{pump} x {int(stages)} stages" if pump and pd.notna(stages) else pump,
        f"{run['INSTALL_TOP_DEPTH_FT']:,.0f} ft TVD" if pd.notna(run.get("INSTALL_TOP_DEPTH_FT")) else "",
        f"run since {pd.Timestamp(run['install_date']):%b %Y}" if pd.notna(run.get("install_date")) else "",
        f"{opt.start} to {opt.end}", FREQ_LABEL.get(opt.freq, opt.freq),
        K_MODE_LABEL.get(opt.k_mode, opt.k_mode),
        "steady rows only" if opt.steady_only else "steady and transient rows",
    ] if x)
    title(ws, well, caption=caption)

    # --- KPI strip: the Overview tiles, values on row 5 under their labels on row 4
    kpis = [("Q virtual last, BFPD", stats.get("rate_last"), "#,##0"),
            ("Q median, BFPD", stats.get("rate_median"), "#,##0"),
            ("Cumulative liquid, bbl", stats.get("cum_bbl"), "#,##0"),
            ("Rows with a rate", stats.get("rate_rows"), "#,##0"),
            ("PHI last", stats.get("phi_last"), "0.000"),
            ("Steady %", stats.get("steady_pct"), "0.0"),
            ("Uptime %", stats.get("uptime_pct"), "0.0"),
            ("Intake - Pb, psi", stats.get("med_PIP_minus_Pb"), "#,##0")]
    for j, (lab, val, fmt) in enumerate(kpis, start=1):
        h = ws.cell(row=4, column=j, value=lab)
        h.font = LABEL_FONT
        h.alignment = Alignment(wrap_text=True, vertical="bottom")
        h.border = BOX
        v = ws.cell(row=5, column=j, value=_clean(val))
        v.font = KPI_FONT
        v.number_format = fmt
        v.border = BOX
    ws.row_dimensions[4].height = 28

    # --- the editable calibration factor, and the defined name the formulas use
    addr, k_value = k_ref if k_ref else (None, np.nan)
    ws.cell(row=K_ROW, column=1, value=f"{opt.k_name} for this well").font = H2_FONT
    kc = ws.cell(row=K_ROW, column=2)
    kc.value = f"={addr}" if (opt.live_formulas and addr) else _clean(k_value)
    kc.number_format = "0.00"
    kc.font = Font(name="Calibri", size=14, bold=True, color=INK)
    kc.fill = PatternFill("solid", fgColor=EDITABLE)
    kc.border = BOX
    kc.comment = Comment(
        "Type a K here to see its effect. The 'Q at K cell' column, the rate chart's dashed line "
        "and the per-test error all follow this cell.\n\nDelete your value and restore the formula "
        "on the Calibration K sheet to go back to the calibrated factor.", "ESP Virtual Rate")
    note = ws.cell(row=K_ROW, column=3,
                   value="Editable. Calibrated value comes from the Calibration K sheet, which is "
                         "itself the median K of this well's non-suspect matched tests."
                   if opt.live_formulas else "Calibrated value (this workbook was exported without formulas).")
    note.font = CAPTION_FONT
    dv = DataValidation(type="decimal", operator="greaterThan", formula1="0", allow_blank=True,
                        showInputMessage=True, showErrorMessage=True)
    dv.promptTitle, dv.prompt = "Calibration factor", "Any positive number. Q = K x X."
    dv.errorTitle, dv.error = "K must be positive", "Q = K x X, so K has to be greater than zero."
    ws.add_data_validation(dv)
    dv.add(kc)
    kname = _table_name("K", well)
    wb.defined_names.add(DefinedName(kname, attr_text=f"{quote_sheetname(ws.title)}!$B${K_ROW}"))
    ws.freeze_panes = ws.cell(row=9, column=1)

    # --- the series behind the charts
    s = well_series(res, well, opt)
    if "Q_app" in s.columns:
        s.insert(list(s.columns).index("Q_app") + 1, "Q_at_K", np.nan)
    heads = {"Q_app": f"Q virtual, BFPD ({K_MODE_LABEL.get(opt.k_mode, opt.k_mode)})",
             "Q_at_K": f"Q at {opt.k_name} cell, BFPD", "K_row": f"{opt.k_label} applied"}
    sblk = write_block(ws, s, header_row=DATA_ROW, table=_table_name("Series", well), headers=heads,
                       widths={"TIME_STAMP": 18})
    if len(s) and "Q_at_K" in sblk.cols:
        xl = sblk.letter("X")
        bl = sblk.letter("B_LIQ") if (opt.wc_correction and "B_LIQ" in sblk.cols) else None
        for i in range(len(s)):
            row = sblk.first_data_row + i
            if bl:
                f = (f'=IF(OR({xl}{row}="",{bl}{row}=""),"",{kname}*{xl}{row}/{bl}{row})'
                     if opt.live_formulas else None)
            else:
                f = f'=IF({xl}{row}="","",{kname}*{xl}{row})' if opt.live_formulas else None
            if f is None:
                v = s["X"].iloc[i]
                if opt.wc_correction and "B_LIQ" in s.columns:
                    b = s["B_LIQ"].iloc[i]
                    v = v / b if pd.notna(b) and b else np.nan
                f = _clean(k_value * v) if pd.notna(v) else None
            cell = ws.cell(row=row, column=sblk.col("Q_at_K"), value=f)
            cell.number_format = "#,##0"

    # --- blocks that feed the charts, laid out to the right of the chart band
    aux = col_after(ws, 1, 2 * CHART_W + 1.5)
    tb = well_test_block(res, well, opt)
    tblk = None
    if len(tb):
        theads = {"K": f"{opt.k_name} at test", "Q_at_K": f"Q at {opt.k_name} cell, BFPD",
                  "APE_at_K": "Error at K cell, %"}
        tblk = write_block(ws, tb, header_row=CHART_TOP, first_col=aux,
                           table=_table_name("Tests", well), headers=theads,
                           style="TableStyleMedium9")
        for i in range(len(tb)):
            row = tblk.first_data_row + i
            q, x = f"{tblk.letter('Q_LIQ')}{row}", f"{tblk.letter('X_at_test')}{row}"
            if opt.live_formulas:
                pred = (f"{kname}*{x}/{tblk.letter('B_LIQ')}{row}"
                        if (opt.wc_correction and "B_LIQ" in tblk.cols) else f"{kname}*{x}")
                ws.cell(row=row, column=tblk.col("Q_at_K"), value=f'=IFERROR({pred},"")')
                ws.cell(row=row, column=tblk.col("APE_at_K"),
                        value=f'=IFERROR(ABS({tblk.letter("Q_at_K")}{row}-{q})/{q}*100,"")')
            else:
                xv = tb["X_at_test"].iloc[i]
                bq = tb["B_LIQ"].iloc[i] if (opt.wc_correction and "B_LIQ" in tb.columns) else 1.0
                pv = k_value * xv / bq if pd.notna(xv) and pd.notna(bq) and bq else np.nan
                ws.cell(row=row, column=tblk.col("Q_at_K"), value=_clean(pv))
                qv = tb["Q_LIQ"].iloc[i]
                ws.cell(row=row, column=tblk.col("APE_at_K"),
                        value=_clean(abs(pv - qv) / qv * 100 if pd.notna(pv) and qv else np.nan))
            ws.cell(row=row, column=tblk.col("Q_at_K")).number_format = "#,##0"
            ws.cell(row=row, column=tblk.col("APE_at_K")).number_format = "0.0"

    # a two-point block so the K cell can be drawn as a horizontal line on the K chart
    kblk = None
    if tblk is not None:
        span = [tb["TEST_TS"].min(), tb["TEST_TS"].max()]
        if len(s) and s["TIME_STAMP"].notna().any():
            span = [min(span[0], s["TIME_STAMP"].min()), max(span[1], s["TIME_STAMP"].max())]
        kline = pd.DataFrame({"TIME_STAMP": span, "K_line": [k_value, k_value]})
        krow = tblk.last_row + 3
        kblk = write_block(ws, kline, header_row=krow, first_col=aux,
                           table=_table_name("Kline", well), style="TableStyleLight9",
                           headers={"K_line": f"{opt.k_name} cell"})
        if opt.live_formulas:
            for i in range(2):
                c = ws.cell(row=kblk.first_data_row + i, column=kblk.col("K_line"), value=f"={kname}")
                c.number_format = "0.00"

    mb = monthly_block(res, well, opt)
    mblk = None
    if len(mb):
        mrow = (kblk.last_row if kblk else (tblk.last_row if tblk else CHART_TOP)) + 3
        mblk = write_block(ws, mb, header_row=mrow, first_col=aux,
                           table=_table_name("Monthly", well), style="TableStyleMedium9",
                           headers={c: opt.category_labels.get(c, c) for c in mb.columns})

    # --- the charts, in the order the app shows them
    charts = []
    if len(s):
        x = sblk.xref("TIME_STAMP")
        rate = [_series(sblk.yref("Q_app"), x, color=color, line=True, width=17000)]
        if "Q_at_K" in sblk.cols:
            rate.append(_series(sblk.yref("Q_at_K"), x, color=ACCENT, line=True, dash="dash",
                                width=15000))
        if tblk is not None:
            rate.append(_series(tblk.yref("Q_test_used"), tblk.xref("TEST_TS"), color=TEST_MARK,
                                line=False, marker="diamond", size=9))
            if tb["suspect"].any():
                rate.append(_series(tblk.yref("Q_test_suspect"), tblk.xref("TEST_TS"),
                                    color=SUSPECT_MARK, line=False, marker="x", size=9))
        charts.append(_finish(time_chart(f"{well} - virtual rate and well tests", "BFPD", rate)))
        if "PHI" in sblk.cols:
            charts.append(_finish(time_chart(
                f"{well} - pump health indicator PHI", "PHI",
                [_series(sblk.yref("PHI"), x, color=INK2, line=True, width=14000)])))
    if tblk is not None:
        kt = "K_dh" if (opt.wc_correction and "K_dh" in tblk.cols) else "K"
        ks = [_series(tblk.yref(kt), tblk.xref("TEST_TS"), color=TEST_MARK, line=False,
                      marker="diamond", size=9)]
        if kblk is not None:
            ks.append(_series(kblk.yref("K_line"), kblk.xref("TIME_STAMP"), color=color,
                              line=True, dash="dash"))
        charts.append(_finish(time_chart(f"{well} - {opt.k_name} at each matched test",
                                         f"{opt.k_name} = Q test / X", ks)))
    if len(s):
        x = sblk.xref("TIME_STAMP")
        press = [_series(sblk.yref(c), x, color=SIGNAL_COLORS[c], line=True, width=14000)
                 for c in ("PIP", "PDP", "WHP") if c in sblk.cols]
        if "Pb" in sblk.cols and s["Pb"].notna().any():
            press.append(_series(sblk.yref("Pb"), x, color=SIGNAL_COLORS["Pb"], line=True,
                                 dash="dash", width=12000))
        if press:
            charts.append(_finish(time_chart(f"{well} - intake, discharge and wellhead pressure",
                                             "psi", press)))
        if "VOLTAGE" in sblk.cols and "AMPERAGE" in sblk.cols:
            charts.append(dual_line(f"{well} - voltage and current", sblk.xref("TIME_STAMP"),
                                    (sblk.yref("VOLTAGE"), SIGNAL_COLORS["VOLTAGE"], "Voltage, V"),
                                    (sblk.yref("AMPERAGE"), SIGNAL_COLORS["AMPERAGE"], "Current, A")))
    if tblk is not None and "WC_FRAC" in tblk.cols and "B_LIQ" in tblk.cols:
        charts.append(dual_line(f"{well} - water cut and B_liq at the well tests",
                                tblk.xref("TEST_TS"),
                                (tblk.yref("WC_FRAC"), MUTED, "Water cut"),
                                (tblk.yref("B_LIQ"), color, "B_liq, rb/stb")))
    if mblk is not None:
        ch = BarChart()
        ch.type, ch.grouping, ch.overlap = "col", "stacked", 100
        ch.title = f"{well} - SCADA rows per month by category"
        ch.height, ch.width = CHART_H, CHART_W
        ch.add_data(Reference(ws, min_col=mblk.first_col + 1, max_col=mblk.last_col,
                              min_row=mblk.header_row, max_row=mblk.last_row), titles_from_data=True)
        ch.set_categories(mblk.xref("month"))
        ch.y_axis.title = "rows per month"
        ch.x_axis.number_format = "yyyy-mm"
        for i, srs in enumerate(ch.series):
            cat = [c for c in mb.columns if c != "month"]
            if i < len(cat) and cat[i] in QUALITY_CATEGORIES:
                srs.graphicalProperties.solidFill = QUALITY_COLORS[QUALITY_CATEGORIES.index(cat[i])]
        charts.append(_finish(ch))
    anchor_charts(ws, charts, top_row=CHART_TOP)
    return ws.title


def dual_line(name: str, cats: Reference, left: tuple, right: tuple,
              date_fmt: str = "yyyy-mm") -> LineChart:
    """Two signals with very different scales on one chart, the second on a secondary axis."""
    lref, lcolor, ltitle = left
    rref, rcolor, rtitle = right
    c1 = LineChart()
    c1.title = name
    c1.height, c1.width = CHART_H, CHART_W
    c1.add_data(lref, titles_from_data=True)
    c1.set_categories(cats)
    c1.y_axis.title = ltitle
    c1.x_axis = DateAxis(crossAx=100)
    c1.x_axis.number_format = date_fmt
    c1.x_axis.majorTimeUnit = "days"
    c1.x_axis.delete = False
    c1.y_axis.delete = False
    c1.x_axis.majorGridlines = None
    c1.dispBlanksAs = "gap"
    c2 = LineChart()
    c2.add_data(rref, titles_from_data=True)
    c2.y_axis.axId = 200
    c2.y_axis.title = rtitle
    c2.y_axis.delete = False
    c2.y_axis.majorGridlines = None
    c1.y_axis.crosses = "max"
    c1 += c2
    for srs, col in zip(c1.series, (lcolor, rcolor)):
        srs.graphicalProperties.line.solidFill = col
        srs.graphicalProperties.line.width = 15000
        srs.marker = Marker(symbol="none")
        srs.smooth = False
    return _finish(c1)


# --------------------------------------------------------------------------- entry points

def build_workbook(res, opt: ExportOptions) -> Workbook:
    """The whole workbook: index, cross-well tables, then one charted tab per well."""
    wb = Workbook()
    wb.calculation.fullCalcOnLoad = True          # the formulas below carry no cached values
    wb.properties.title = "ESP Virtual Rate export"
    wb.properties.subject = res.meta.get("dataset_label", "")
    wb.properties.creator = "ESP Virtual Rate Simulator"
    wb.properties.description = (f"Wells {', '.join(opt.wells)}; {opt.start} to {opt.end}; "
                                 f"{K_MODE_LABEL.get(opt.k_mode, opt.k_mode)}; "
                                 f"{FREQ_LABEL.get(opt.freq, opt.freq)}.")

    readme, next_row = readme_sheet(wb, res, opt)
    index = []

    analysed = set(res.wells_analysed)
    stats = {}
    for w in opt.wells:
        d = vr.slice_period(res.rt, w, opt.start, opt.end)
        stats[w] = vr.period_stats(d, opt.k_mode, opt.steady_only, opt.wc_correction)
    cal_by_well = {}
    if len(res.cal):
        for w, g in res.cal[res.cal["WELL_NAME"].isin(opt.wells)].groupby("WELL_NAME"):
            cal_by_well[w] = g.sort_values("regime").iloc[-1]

    index.append((summary_sheet(wb, res, opt, stats, cal_by_well),
                  "Every Overview tile, one row per well, plus rate and error comparison charts."))
    m_ws, m_blk, ranges = matched_sheet(wb, res, opt)
    k_refs, cal_title = calibration_sheet(wb, res, opt, m_ws, m_blk, ranges)
    index.append((cal_title, "K per well and regime. Live: the median of the matched tests."))
    index.append((m_ws.title, "Every matched well test, with K = Q test / X as a formula."))
    index.append((tests_sheet(wb, res, opt), "All well tests loaded, matched or not, with the reason."))
    index.append((validation_sheet(wb, res, opt), "Per-test predictions of each method and their errors."))
    index.append((errors_sheet(wb, res, opt), "MAPE and median error by method, overall, per well and per rule."))
    if opt.include_quality:
        index.append((quality_sheet(wb, res, opt), "Row counts per flag and per month category."))
    if opt.include_events:
        index.append((events_sheet(wb, res, opt), "Diagnosis events overlapping the period, with evidence."))
    if opt.include_pvt and (len(res.lab_pvt) or len(res.gas) or len(res.test_pvt)):
        index.append((pvt_sheet(wb, res, opt), "Laboratory PVT, intake vs bubble point, water cut per test."))
    index.append((reference_sheet(wb, res, opt), "Pump runs, electrical regimes and excluded wells."))

    if opt.per_well_tabs:
        for w in opt.wells:
            if w not in analysed:
                continue
            name = well_sheet(wb, res, opt, w, k_refs.get(w), stats.get(w, {}))
            index.append((name, f"{w}: tiles, editable K, every chart the app draws, and the series."))
    index_on_readme(readme, next_row, index)
    return wb


def workbook_bytes(res, opt: ExportOptions) -> bytes:
    buf = io.BytesIO()
    build_workbook(res, opt).save(buf)
    return buf.getvalue()


def suggested_filename(res, opt: ExportOptions) -> str:
    ds = re.sub(r"[^0-9A-Za-z]+", "-", res.meta.get("dataset", "export")).strip("-")
    return f"esp_virtual_rate_{ds}_{opt.start}_{opt.end}.xlsx"


def estimated_rows(res, opt: ExportOptions) -> int:
    """Rows the per-well tabs will hold, so the app can warn before a slow 30-minute export."""
    total = 0
    for w in opt.wells:
        d = vr.slice_period(res.rt, w, opt.start, opt.end)
        r = vr.rate_rows(d, opt.steady_only)
        if opt.freq == "30min":
            total += len(r)
        elif len(r):
            span = r["TIME_STAMP"].max() - r["TIME_STAMP"].min()
            per_day = 24 if opt.freq == "h" else 1
            total += int(span.total_seconds() / 86400 * per_day) + 1
    return total
