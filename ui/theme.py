"""Colours and Plotly layout conventions (reference palette, fixed categorical order)."""
from __future__ import annotations

import plotly.graph_objects as go

from core.config import WELLS_ALL

# Categorical slots from the validated reference palette, in fixed order. A well keeps its
# colour whatever the selection is; the mapping is positional over WELLS_ALL so a new or
# un-excluded well gets a slot without any code change.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7", "#eda100", "#e87ba4", "#008300", "#e34948"]
PALETTE_RGB = [(42, 120, 214), (235, 104, 52), (27, 175, 122), (74, 58, 167),
               (237, 161, 0), (232, 123, 164), (0, 131, 0), (227, 73, 72)]
WELL_COLORS = {w: PALETTE[i % len(PALETTE)] for i, w in enumerate(WELLS_ALL)}
WELL_LIGHT = {w: "rgba({}, {}, {}, 0.35)".format(*PALETTE_RGB[i % len(PALETTE_RGB)])
              for i, w in enumerate(WELLS_ALL)}
EXCLUDED_COLOR = "#898781"        # excluded wells are drawn in muted grey, never in a series hue


def well_color(well: str, excluded: bool = False) -> str:
    if excluded:
        return EXCLUDED_COLOR
    return WELL_COLORS.get(well, PALETTE[abs(hash(well)) % len(PALETTE)])


def well_light(well: str) -> str:
    return WELL_LIGHT.get(well, "rgba(137,135,129,0.35)")


METHOD_COLORS = {"M1_LOO": "#2a78d6", "M6_LOO": "#86b6ef",          # M6 is a lighter step of M1's hue
                 "M2_WALK": "#eb6834", "M6_WALK": "#f0a687",        # ... and of M2's
                 "BASE_LAST_TEST": "#898781"}
METHOD_LABELS = {"M1_LOO": "M1 single-K (LOO)", "M6_LOO": "M6 water-cut corrected (LOO)",
                 "M2_WALK": "M2 walk-forward", "M6_WALK": "M6 water-cut corrected (walk-forward)",
                 "BASE_LAST_TEST": "Baseline last test"}
BASE_METHODS = ["M1_LOO", "M2_WALK", "BASE_LAST_TEST"]
WC_ALL_METHODS = ["M1_LOO", "M6_LOO", "M2_WALK", "M6_WALK", "BASE_LAST_TEST"]

# status colours (reserved; never used for a series)
GOOD, WARNING, SERIOUS, CRITICAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
MUTED, GRID, INK2, INK = "#898781", "#e1e0d9", "#52514e", "#0b0b0b"
TEST_MARK = "#0b0b0b"      # matched-test diamond: ink with a white ring, distinct from every series colour

# row-category colours for the data-quality stack (fixed mapping)
CATEGORY_COLORS = {
    "steady": "#2a78d6",
    "transient": "#86b6ef",
    "uncalibrated_basis": "#c3c2b7",
    "pump_off": "#52514e",
    "missing_press": "#eb6834",
    "missing_elec": "#e87ba4",
    "bad_dP": "#eda100",
    "bad_press_range": "#e34948",
    "bad_freq": "#4a3aa7",
}
CATEGORY_LABELS = {
    "steady": "Steady (rate computed)",
    "transient": "Transient (usable, not steady)",
    "uncalibrated_basis": "Steady but off calibrated V basis",
    "pump_off": "Pump off",
    "missing_press": "Missing PIP/PDP",
    "missing_elec": "Missing V/I",
    "bad_dP": "dP <= 300 psi",
    "bad_press_range": "Pressure out of range",
    "bad_freq": "Frequency out of range",
}
EVENT_COLORS = {
    "SCADA_GAP": "#c3c2b7", "PUMP_OFF": "#52514e", "VOLTAGE_BASIS_CHANGE": "#eb6834", "VOLTAGE_STEP": "#eda100",
    "TEMP_UNIT_SWITCH": "#4a3aa7", "RATE_STEP": "#2a78d6", "PHI_DRIFT": "#d03b3b", "BACKPRESSURE": "#ec835a",
    "LOW_PIP_TREND": "#1baf7a", "SUSPECT_TEST": "#e34948",
}
# intake vs bubble point (spec addendum B)
GAS_COLORS = {"ok": GOOD, "watch": WARNING, "gassy": CRITICAL, "unknown": MUTED}
GAS_BADGE = {"ok": "green", "watch": "orange", "gassy": "red", "unknown": "gray"}
GAS_LABEL = {"ok": "above Pb", "watch": "near Pb", "gassy": "gassy", "unknown": "no PVT"}

SEVERITY_ICON = {"info": ":material/info:", "warning": ":material/warning:", "critical": ":material/error:"}
SEVERITY_BADGE = {"info": "blue", "warning": "orange", "critical": "red"}


def base_layout(fig: go.Figure, height: int = 380, title: str | None = None, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        title=dict(text=title, x=0, xanchor="left", font=dict(size=18)) if title else None,
        margin=dict(l=8, r=8, t=52 if title else 30, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        hoverlabel=dict(bgcolor="white", font=dict(size=14), namelength=-1),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, font=dict(size=13), bgcolor="rgba(0,0,0,0)"),
        showlegend=legend,
        font=dict(size=14),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False, showline=True, linecolor="#c3c2b7",
                     tickfont=dict(color=INK2, size=13))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False, showline=False,
                     tickfont=dict(color=INK2, size=13), title_font=dict(color=INK2, size=13))
    return fig


def fmt_num(x, nd: int = 0, unit: str = "") -> str:
    try:
        if x is None or x != x:
            return "n/a"
        return f"{x:,.{nd}f}{unit}"
    except Exception:
        return "n/a"
