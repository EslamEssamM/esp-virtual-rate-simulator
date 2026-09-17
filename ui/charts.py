"""Plotly figure builders. Pure presentation: every number comes from core/ or ui.data."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core import config as C
from core.calibration import k_interp
from core.virtual_rate import q_col
from ui.data import FREQ_LABEL
from ui.theme import (BASE_METHODS, CATEGORY_COLORS, CATEGORY_LABELS, CRITICAL, GOOD, GRID, INK2, METHOD_COLORS,
                      METHOD_LABELS, MUTED, SERIOUS, TEST_MARK, WARNING, base_layout, well_color, well_light)

CATEGORY_ORDER = list(CATEGORY_COLORS)


def _left_align_titles(fig, size):
    """Left-align subplot titles only (paper-referenced); leave data-referenced annotations alone."""
    for a in fig.layout.annotations:
        if a.xref == "paper" and a.yref == "paper":
            a.update(x=0, xanchor="left", font=dict(size=size, color=INK2))


def _add_intervals(fig, intervals, color, opacity, label=None, row="all", position="top left"):
    """Shade [a, b) intervals; the label is written once, on the widest interval."""
    if not intervals:
        return
    widest = max(range(len(intervals)), key=lambda i: intervals[i][1] - intervals[i][0])
    for i, (a, b) in enumerate(intervals):
        kw = {}
        if label and i == widest:
            kw = dict(annotation_text=label, annotation_position=position, annotation_font=dict(size=12, color=INK2))
        fig.add_vrect(x0=a, x1=b, fillcolor=color, opacity=opacity, line_width=0, layer="below", row=row, col=1, **kw)


def rate_chart(well: str, series: pd.DataFrame, tests: pd.DataFrame, freq: str, steady_only: bool, k_mode: str,
               shade: dict, height: int = 480, zoom: tuple | None = None, title: str | None = None,
               wc_correction: bool = False, analyst: pd.DataFrame | None = None) -> go.Figure:
    """Q_virtual (top) and PHI (bottom) with well tests, gaps, uncalibrated basis and run markers."""
    q = q_col(k_mode, wc_correction)
    color = well_color(well)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.74, 0.26], vertical_spacing=0.05)

    if freq == "30min" and not series.empty:
        s = series.copy()
        s.loc[~s["rate_steady"].fillna(True).astype(bool), q] = np.nan
        fig.add_scatter(x=s["TIME_STAMP"], y=s[q], mode="lines", name="Q virtual (steady rows)",
                        line=dict(color=color, width=1.4), connectgaps=False,
                        hovertemplate="%{y:,.0f} BFPD<extra>Q virtual</extra>", row=1, col=1)
        if not steady_only:
            t = series[~series["rate_steady"].fillna(True).astype(bool)]
            if len(t):
                fig.add_scatter(x=t["TIME_STAMP"], y=t[q], mode="markers", name="Q virtual (transient rows)",
                                marker=dict(color=well_light(well), size=5),
                                hovertemplate="%{y:,.0f} BFPD<extra>transient</extra>", row=1, col=1)
    elif not series.empty:
        fig.add_scatter(x=series["TIME_STAMP"], y=series[q], mode="lines",
                        name=("Q virtual, B_liq corrected" if wc_correction else "Q virtual")
                             + f" ({FREQ_LABEL[freq]}{'' if steady_only else ', incl. transient'})",
                        line=dict(color=color, width=1.8), connectgaps=False,
                        hovertemplate="%{y:,.0f} BFPD<extra>Q virtual</extra>", row=1, col=1)

    # well tests: every test as an open circle, matched as filled diamond, suspect as red x
    if tests is not None and len(tests):
        other = tests[tests["MATCH"] != "MATCHED"]
        good = tests[(tests["MATCH"] == "MATCHED") & ~tests["suspect"].fillna(False).astype(bool)]
        bad = tests[(tests["MATCH"] == "MATCHED") & tests["suspect"].fillna(False).astype(bool)]
        if len(other):
            fig.add_scatter(x=other["TEST_TS"], y=other["Q_LIQ"], mode="markers", name="Well test (no SCADA match)",
                            marker=dict(symbol="circle-open", size=11, color=MUTED, line=dict(width=2)),
                            customdata=other["MATCH"], hovertemplate="test %{y:,.0f} BFPD (%{customdata})<extra></extra>",
                            row=1, col=1)
        if len(good):
            fig.add_scatter(x=good["TEST_TS"], y=good["Q_LIQ"], mode="markers", name="Matched test (used for K)",
                            marker=dict(symbol="diamond", size=15, color=TEST_MARK, line=dict(width=2.5, color="white")),
                            customdata=good["K"], hovertemplate="test %{y:,.0f} BFPD, K=%{customdata:.2f}<extra></extra>",
                            row=1, col=1)
        if len(bad):
            fig.add_scatter(x=bad["TEST_TS"], y=bad["Q_LIQ"], mode="markers", name="Suspect test (excluded)",
                            marker=dict(symbol="x", size=16, color=CRITICAL, line=dict(width=2, color=CRITICAL)),
                            customdata=bad["K"], hovertemplate="suspect test %{y:,.0f} BFPD, K=%{customdata:.2f}<extra></extra>",
                            row=1, col=1)

    # the previous analyst's workbook series, for comparison only
    if analyst is not None and len(analyst):
        for col, name, dash in [("Q_ANALYST_CALIBRATED", "previous analyst (workbook)", "dash"),
                                ("Q_LIQ_ALLOCATED", "previous analyst (allocated)", "dot")]:
            if col in analyst.columns and analyst[col].notna().any():
                fig.add_scatter(x=analyst["TIME_STAMP"], y=analyst[col], mode="lines", name=name,
                                line=dict(color=MUTED, width=1.6, dash=dash), connectgaps=False,
                                hovertemplate="%{y:,.0f} BFPD<extra>" + name + "</extra>", row=1, col=1)

    # PHI strip
    if not series.empty and "PHI" in series:
        fig.add_hrect(y0=0.95, y1=1.05, fillcolor=GOOD, opacity=0.10, line_width=0, layer="below", row=2, col=1)
        fig.add_scatter(x=series["TIME_STAMP"], y=series["PHI"], mode="lines", name="PHI",
                        line=dict(color=INK2, width=1.3), connectgaps=False,
                        hovertemplate="PHI %{y:.3f}<extra></extra>", row=2, col=1)

    _add_intervals(fig, shade.get("gaps", []), "#898781", 0.14, "SCADA gap", position="top right")
    _add_intervals(fig, shade.get("uncalibrated", []), WARNING, 0.16, "uncalibrated V basis")
    _add_intervals(fig, shade.get("previous_run", []), "#4a3aa7", 0.07, "previous pump run", position="bottom left")
    for i, t in enumerate(shade.get("regimes", [])):
        fig.add_vline(x=t, line=dict(color=SERIOUS, width=1.8, dash="dot"), row="all", col=1)
        fig.add_annotation(x=t, y=0.97, yref="paper", text=f"regime {i + 2} starts {pd.Timestamp(t):%d %b %Y}",
                           showarrow=False, xanchor="left", font=dict(size=12, color=SERIOUS), yanchor="top",
                           bgcolor="rgba(255,255,255,0.75)")
    inst = shade.get("install_date")
    run = shade.get("run", {})
    if inst is not None and pd.notna(inst) and not series.empty:
        x0, x1 = series["TIME_STAMP"].min(), series["TIME_STAMP"].max()
        if x0 <= inst <= x1:
            fig.add_vline(x=inst, line=dict(color="#4a3aa7", width=1.5, dash="dash"), row="all", col=1)
            fig.add_annotation(x=inst, y=1.0, yref="paper", text=f"new run {inst:%d %b %Y}: {run.get('CANONICAL_MODEL', '')} x{run.get('NUMBER_OF_STAGES', '')}",
                               showarrow=False, xanchor="left", font=dict(size=12, color="#4a3aa7"), yanchor="bottom")

    base_layout(fig, height=height, title=title)
    fig.update_yaxes(title_text="BFPD", row=1, col=1, rangemode="tozero")
    fig.update_yaxes(title_text="PHI", row=2, col=1)
    fig.update_xaxes(showspikes=True, spikemode="across", spikethickness=1, spikecolor=MUTED, spikedash="dot")
    if zoom:
        fig.update_xaxes(range=[pd.Timestamp(zoom[0]), pd.Timestamp(zoom[1])])
    return fig


SIGNALS = [("VOLTAGE", "Voltage, V"), ("AMPERAGE", "Current, A"), ("FREQ_FILLED", "Frequency, Hz (filled)"),
           ("PIP", "PIP, psi"), ("PDP", "PDP, psi"), ("WHP", "WHP, psi"), ("MT_F", "Motor temp, degF")]


def signal_viewer(well: str, s: pd.DataFrame, freq: str, pump_off: list, gaps: list, height: int = 1000) -> go.Figure:
    fig = make_subplots(rows=len(SIGNALS), cols=1, shared_xaxes=True, vertical_spacing=0.025,
                        subplot_titles=[t for _, t in SIGNALS])
    color = well_color(well)
    for i, (c, label) in enumerate(SIGNALS, start=1):
        if c in s.columns:
            fig.add_scatter(x=s["TIME_STAMP"], y=s[c], mode="lines", name=label, line=dict(color=color, width=1.2),
                            connectgaps=False, hovertemplate="%{y:,.1f}<extra>" + label + "</extra>", row=i, col=1)
    if "FREQUENCY" in s.columns and freq == "30min":
        raw = s.dropna(subset=["FREQUENCY"])
        fig.add_scatter(x=raw["TIME_STAMP"], y=raw["FREQUENCY"], mode="markers", name="Frequency (raw readings)",
                        marker=dict(color=INK2, size=3), hovertemplate="%{y:.1f} Hz raw<extra></extra>", row=3, col=1)
    _add_intervals(fig, pump_off, "#52514e", 0.15, "pump off")
    _add_intervals(fig, gaps, "#898781", 0.10, "SCADA gap", position="top right")
    base_layout(fig, height=height, legend=False)
    _left_align_titles(fig, 13)
    fig.update_xaxes(showspikes=True, spikemode="across", spikethickness=1, spikecolor=MUTED, spikedash="dot")
    return fig


def quality_stack(mf: pd.DataFrame, wells: list[str], height_per_well: int = 210) -> go.Figure:
    """Stacked monthly row counts by category, one row per well, shared x."""
    n = max(len(wells), 1)
    fig = make_subplots(rows=n, cols=1, shared_xaxes=True, vertical_spacing=0.09 / n * 2, subplot_titles=wells)
    for i, w in enumerate(wells, start=1):
        g = mf[mf["WELL_NAME"] == w]
        piv = g.pivot_table(index="month", columns="category", values="rows", aggfunc="sum", fill_value=0)
        for cat in CATEGORY_ORDER:
            if cat in piv.columns:
                fig.add_bar(x=piv.index, y=piv[cat], name=CATEGORY_LABELS[cat], marker_color=CATEGORY_COLORS[cat],
                            legendgroup=cat, showlegend=(i == 1), offsetgroup=0,
                            hovertemplate="%{y:,} rows<extra>" + CATEGORY_LABELS[cat] + "</extra>", row=i, col=1)
    fig.update_layout(barmode="stack", bargap=0.15)
    base_layout(fig, height=height_per_well * n + 110)
    fig.update_layout(legend=dict(orientation="h", y=-0.12 / n * 2 - 0.02, yanchor="top"), margin=dict(t=30))
    _left_align_titles(fig, 14)
    fig.update_yaxes(title_text="rows / month")
    return fig


def k_chart(well: str, mm_w: pd.DataFrame, cal_w, start, end, height: int = 340) -> go.Figure:
    """K at each matched test, the interpolated curve, and one K_single per electrical regime.

    A well that changed transformer ratio or stage count mid-life has one calibration row per
    regime, so its curve is drawn in segments rather than as a single line across the change.
    """
    fig = go.Figure()
    color = well_color(well)
    cal_w = pd.DataFrame() if cal_w is None else (cal_w.to_frame().T if isinstance(cal_w, pd.Series) else cal_w)
    multi = len(cal_w) > 1
    if len(cal_w) and len(mm_w):
        for _, cr in cal_w.iterrows():
            regime = int(cr["regime"]) if "regime" in cr.index and pd.notna(cr["regime"]) else 1
            mr = mm_w[mm_w["regime"] == regime] if "regime" in mm_w.columns and multi else mm_w
            if not len(mr):
                continue
            lo = mr["TEST_TS"].min() if multi else pd.Timestamp(start)
            hi = mr["TEST_TS"].max() if multi else pd.Timestamp(end)
            grid = pd.DatetimeIndex([lo, hi]) if lo == hi else pd.date_range(lo, hi, freq="D")
            suffix = f" (regime {regime})" if multi else ""
            fig.add_scatter(x=grid, y=k_interp(pd.Series(grid), mr), mode="lines",
                            name="K interpolated" + suffix, line=dict(color=color, width=2),
                            hovertemplate="K interp %{y:.2f}<extra></extra>")
            fig.add_scatter(x=[grid[0], grid[-1]], y=[float(cr["K_single"])] * 2, mode="lines",
                            name=f"K single {float(cr['K_single']):.1f}" + suffix,
                            line=dict(color=color, width=1.2, dash="dash"),
                            hovertemplate="K single %{y:.2f}<extra></extra>")
    good = mm_w[~mm_w["suspect"]]
    bad = mm_w[mm_w["suspect"]]
    if len(good):
        fig.add_scatter(x=good["TEST_TS"], y=good["K"], mode="markers", name="K at matched test",
                        marker=dict(symbol="diamond", size=15, color=TEST_MARK, line=dict(width=2.5, color="white")),
                        customdata=np.stack([good["Q_LIQ"], good["RT_X"]], axis=1),
                        hovertemplate="K %{y:.2f} (Q %{customdata[0]:,.0f} BFPD, X %{customdata[1]:.2f})<extra></extra>")
    if len(bad):
        fig.add_scatter(x=bad["TEST_TS"], y=bad["K"], mode="markers", name="Suspect (excluded)",
                        marker=dict(symbol="x", size=16, color=CRITICAL, line=dict(width=2, color=CRITICAL)),
                        customdata=np.stack([bad["Q_LIQ"], bad["robust_z"]], axis=1),
                        hovertemplate="K %{y:.2f} (Q %{customdata[0]:,.0f} BFPD, robust z %{customdata[1]:.1f})<extra></extra>")
    base_layout(fig, height=height, title=well)
    fig.update_yaxes(title_text="K = Q_test / X")
    return fig


def validation_chart(v: pd.DataFrame, height: int = 420, methods: list[str] | None = None) -> go.Figure:
    v = v.sort_values(["WELL_NAME", "TEST_TS"]).reset_index(drop=True)
    labels = [f"{w.replace('SA-', '').replace('_T', '')}<br>{t:%b %y}" for w, t in zip(v["WELL_NAME"], v["TEST_TS"])]
    fig = go.Figure()
    for m in (methods or BASE_METHODS):
        fig.add_bar(x=labels, y=v["APE_" + m], name=METHOD_LABELS[m], marker_color=METHOD_COLORS[m],
                    marker_line=dict(width=0), hovertemplate="%{y:.1f} %<extra>" + METHOD_LABELS[m] + "</extra>")
    for i, r in v.iterrows():
        if r["suspect"]:
            fig.add_annotation(x=labels[i], y=max(r[["APE_" + m for m in (methods or BASE_METHODS)]].max(), 0),
                               text="suspect", showarrow=False, yshift=12, font=dict(size=12, color=CRITICAL))
    fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.05)
    base_layout(fig, height=height)
    fig.update_layout(hovermode="x")
    fig.update_yaxes(title_text="absolute % error vs well test")
    fig.update_xaxes(tickfont=dict(size=12), tickangle=0)
    return fig


def _add_test_markers(fig, tests: pd.DataFrame, row=None, col=None):
    """Measured well tests: open circle = no SCADA match, ink diamond = used for K, red x = suspect."""
    if tests is None or len(tests) == 0:
        return
    kw = dict(row=row, col=col) if row else {}
    other = tests[tests["MATCH"] != "MATCHED"]
    good = tests[(tests["MATCH"] == "MATCHED") & ~tests["suspect"].fillna(False).astype(bool)]
    bad = tests[(tests["MATCH"] == "MATCHED") & tests["suspect"].fillna(False).astype(bool)]
    if len(other):
        fig.add_scatter(x=other["TEST_TS"], y=other["Q_LIQ"], mode="markers", name="Well test (no SCADA match)",
                        marker=dict(symbol="circle-open", size=11, color=MUTED, line=dict(width=2)),
                        customdata=other["MATCH"], hovertemplate="test %{y:,.0f} BFPD (%{customdata})<extra></extra>", **kw)
    if len(good):
        fig.add_scatter(x=good["TEST_TS"], y=good["Q_LIQ"], mode="markers", name="Matched test (used for K)",
                        marker=dict(symbol="diamond", size=15, color=TEST_MARK, line=dict(width=2.5, color="white")),
                        customdata=good["K"], hovertemplate="test %{y:,.0f} BFPD, K=%{customdata:.2f}<extra></extra>", **kw)
    if len(bad):
        fig.add_scatter(x=bad["TEST_TS"], y=bad["Q_LIQ"], mode="markers", name="Suspect test (excluded)",
                        marker=dict(symbol="x", size=16, color=CRITICAL, line=dict(width=2, color=CRITICAL)),
                        customdata=bad["K"], hovertemplate="suspect test %{y:,.0f} BFPD, K=%{customdata:.2f}<extra></extra>", **kw)


def whatif_chart(well: str, series: pd.DataFrame, k_ref: float, k_new: float, freq: str,
                 tests: pd.DataFrame | None = None, whatif_at_tests: pd.DataFrame | None = None,
                 height: int = 360) -> go.Figure:
    fig = go.Figure()
    color = well_color(well)
    if not series.empty:
        fig.add_scatter(x=series["TIME_STAMP"], y=series["K_single"] * series["X"], mode="lines",
                        name=f"K single {k_ref:.2f}", line=dict(color=MUTED, width=1.4), connectgaps=False,
                        hovertemplate="%{y:,.0f} BFPD<extra>K single</extra>")
        fig.add_scatter(x=series["TIME_STAMP"], y=k_new * series["X"], mode="lines",
                        name=f"What-if K {k_new:.2f}", line=dict(color=color, width=2), connectgaps=False,
                        hovertemplate="%{y:,.0f} BFPD<extra>what-if</extra>")
    _add_test_markers(fig, tests)
    if whatif_at_tests is not None and len(whatif_at_tests):
        fig.add_scatter(x=whatif_at_tests["TEST_TS"], y=whatif_at_tests["Q_whatif"], mode="markers",
                        name="What-if prediction at test",
                        marker=dict(symbol="diamond-open", size=15, color=color, line=dict(width=2.5)),
                        customdata=whatif_at_tests["APE_whatif"],
                        hovertemplate="what-if %{y:,.0f} BFPD (APE %{customdata:.1f} %)<extra></extra>")
    base_layout(fig, height=height)
    fig.update_yaxes(title_text="BFPD", rangemode="tozero")
    return fig


# --------------------------------------------------------------------------- PVT (spec addendum)

def watercut_chart(well: str, test_pvt: pd.DataFrame, daily: pd.DataFrame, height: int = 320) -> go.Figure:
    """Water cut at each well test (points) and the interpolated B_liq actually used (line)."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    color = well_color(well)
    if daily is not None and len(daily) and "B_LIQ" in daily.columns:
        g = daily.dropna(subset=["B_LIQ"])
        fig.add_scatter(x=g["day"], y=g["B_LIQ"], mode="lines", name="B_liq used (interpolated)",
                        line=dict(color=color, width=2), connectgaps=False,
                        hovertemplate="B_liq %{y:.4f}<extra></extra>", secondary_y=True)
    if test_pvt is not None and len(test_pvt):
        fig.add_scatter(x=test_pvt["TEST_TS"], y=test_pvt["WC_FRAC"] * 100, mode="markers+lines",
                        name="Water cut at test", line=dict(color=MUTED, width=1, dash="dot"),
                        marker=dict(symbol="circle", size=11, color=MUTED, line=dict(width=2, color="white")),
                        hovertemplate="water cut %{y:.1f} %<extra></extra>", secondary_y=False)
        fig.add_scatter(x=test_pvt["TEST_TS"], y=test_pvt["B_LIQ"], mode="markers", name="B_liq at test",
                        marker=dict(symbol="diamond", size=13, color=TEST_MARK, line=dict(width=2.5, color="white")),
                        hovertemplate="B_liq %{y:.4f}<extra></extra>", secondary_y=True)
    base_layout(fig, height=height, title=well)
    fig.update_yaxes(title_text="water cut, %", secondary_y=False, rangemode="tozero")
    fig.update_yaxes(title_text="B_liq, rb/stb", secondary_y=True, showgrid=False)
    return fig


def intake_vs_pb_chart(well: str, s: pd.DataFrame, pb: float, gas: dict | None = None,
                       height: int = 360) -> go.Figure:
    """Intake pressure against the bubble point, with the estimated gas fraction on a second axis."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    color = well_color(well)
    if len(s):
        fig.add_scatter(x=s["TIME_STAMP"], y=s["PIP"], mode="lines", name="PIP",
                        line=dict(color=color, width=1.6), connectgaps=False,
                        hovertemplate="PIP %{y:,.0f} psi<extra></extra>", secondary_y=False)
        if "gvf_est" in s.columns and s["gvf_est"].notna().any():
            fig.add_scatter(x=s["TIME_STAMP"], y=s["gvf_est"] * 100, mode="lines",
                            name=f"Estimated gas fraction at intake (gas gravity {C.GAS_GRAVITY} assumed)",
                            line=dict(color=SERIOUS, width=1.4, dash="dot"), connectgaps=False,
                            hovertemplate="GVF ~%{y:.0f} %<extra></extra>", secondary_y=True)
    if pb is not None and np.isfinite(pb):
        fig.add_hline(y=pb, line=dict(color=CRITICAL, width=2, dash="dash"),
                      annotation_text=f"bubble point {pb:,.0f} psi", annotation_position="top left",
                      annotation_font=dict(size=12, color=CRITICAL), secondary_y=False)
    base_layout(fig, height=height)
    fig.update_yaxes(title_text="pressure, psi", secondary_y=False, rangemode="tozero")
    fig.update_yaxes(title_text="estimated GVF, %", secondary_y=True, showgrid=False, rangemode="tozero")
    return fig
