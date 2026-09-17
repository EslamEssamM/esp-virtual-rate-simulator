import pandas as pd
import streamlit as st

from core import config as C
from ui import data as D
from ui.sidebar import filters, set_rules
from ui.theme import METHOD_LABELS

f = filters()
scope = f["scope"]
current = scope.rules
res = D.get_results(scope)

st.title("Filter rules", anchor=False)
st.caption("Every constraint the app puts on a SCADA row, with the value it uses and what it is "
           "rejecting. Change one and the whole pipeline re-runs on it: flags, matching, K, the "
           "error against the well tests, the events and the exports. Nothing is hard-coded "
           "downstream, so a rule can be loosened or switched off and its effect measured rather "
           "than argued about.")

if not current.is_default:
    st.warning("The app is running on changed rules. The numbers on every page are computed under "
               "them, not under the defaults the README quotes.", icon=":material/rule_settings:")

# ---------------------------------------------------------------- what each rule rejects now
st.subheader("What each rule rejects right now", anchor=False)
d = res.rt
pump_on = d[~d["pump_off"]]
rule_rows = [
    ("dP floor", "bad_dP", f"dP = PDP - PIP must exceed {current.min_dp:g} psi"
     if current.min_dp else "off: dP only has to be positive"),
    ("Pressure range", "bad_press_range",
     f"PIP inside {current.pip_lo:g}-{current.pip_hi:g} psi"
     + (f", PDP below {current.pdp_max:g} psi" if current.pdp_max else "")
     + (", and WHP not above PDP" if current.whp_over_pdp else "")),
    ("Pump off", "pump_off",
     f"VOLTAGE < {current.pump_off_v:g} V, or AMPERAGE < {current.pump_off_i:g} A, or FREQUENCY = 0"),
    ("Frequency range", "bad_freq",
     f"FREQUENCY, when present and not 0, inside {current.freq_lo:g}-{current.freq_hi:g} Hz"
     if current.freq_gate else "off: frequency never rejects a row"),
    ("Transient", "transient",
     f"rolling {current.transient_window}-sample CV of current or dP above "
     f"{current.transient_cv * 100:g} %" if current.transient_gate
     else "off: no row counts as transient, so steady = usable"),
    ("Missing electrical", "missing_elec", "VOLTAGE or AMPERAGE null: X cannot be formed"),
    ("Missing pressure", "missing_press", "PIP or PDP null"),
]
tbl = pd.DataFrame([
    dict(rule=name, rows=int(d[col].sum()), pct=float(d[col].mean() * 100),
         pump_on_rows=int(pump_on[col].sum()), pump_on_pct=float(pump_on[col].mean() * 100),
         what=what)
    for name, col, what in rule_rows if col in d.columns])
st.dataframe(tbl, hide_index=True, column_config={
    "rule": st.column_config.TextColumn("Rule", pinned=True),
    "rows": st.column_config.NumberColumn("Rows flagged", format="localized"),
    "pct": st.column_config.ProgressColumn("of all rows", min_value=0, max_value=100, format="%.1f %%"),
    "pump_on_rows": st.column_config.NumberColumn("Of pump-on rows", format="localized",
                                                  help="The same rule counted only over rows where "
                                                       "the pump is running. A rule whose two counts "
                                                       "differ a lot is mostly re-flagging rows that "
                                                       "the pump-off rule already removed."),
    "pump_on_pct": st.column_config.ProgressColumn("of pump-on rows", min_value=0, max_value=100,
                                                   format="%.1f %%"),
    "what": st.column_config.TextColumn("Rule as it stands", width="large")})
st.caption("Flags overlap: a row can be pump off and fail the dP rule at the same time, so the "
           "column does not sum to the row count. Only a row that passes every rule, is steady and "
           "sits on the calibrated voltage basis carries a virtual rate.")

# ---------------------------------------------------------------- the editor
st.subheader("Change the rules", anchor=False)
st.caption("Nothing is applied until you press Apply. Re-running the pipeline takes a few seconds.")

with st.form("rules_form", border=True):
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Pressure**")
        min_dp = st.number_input("dP floor, psi", min_value=0.0, max_value=3000.0,
                                 value=float(current.min_dp), step=25.0,
                                 help="A row is rejected when PDP - PIP does not exceed this. "
                                      "Set it to 0 to switch the floor off; dP still has to be "
                                      "positive, because X = sqrt(3) x V x I / dP is infinite at "
                                      "dP = 0 and negative below it. Default 300.")
        pip_lo = st.number_input("PIP minimum, psi", min_value=0.0, max_value=5000.0,
                                 value=float(current.pip_lo), step=10.0)
        pip_hi = st.number_input("PIP maximum, psi", min_value=100.0, max_value=20000.0,
                                 value=float(current.pip_hi), step=100.0)
        pdp_max = st.number_input("PDP ceiling, psi", min_value=0.0, max_value=60000.0,
                                  value=float(current.pdp_max), step=100.0,
                                  help="0 means no ceiling, which is the default: discharge "
                                       "pressure spans too wide a range across fields to bound "
                                       "sensibly. Set one when a gauge is pegged at its rail - "
                                       "a pegged reading holds a plausible dP, so the dP floor "
                                       "cannot catch it. See the note at the foot of this page.")
        whp_over_pdp = st.toggle("Reject rows where WHP > PDP", value=bool(current.whp_over_pdp),
                                 help="Wellhead pressure above discharge pressure is physically "
                                      "impossible on a producing well and means a bad tag.")
    with c2:
        st.markdown("**Pump running**")
        pump_off_v = st.number_input("Pump off below, V", min_value=0.0, max_value=5000.0,
                                     value=float(current.pump_off_v), step=10.0)
        pump_off_i = st.number_input("Pump off below, A", min_value=0.0, max_value=500.0,
                                     value=float(current.pump_off_i), step=1.0)
        freq_gate = st.toggle("Apply the frequency range", value=bool(current.freq_gate),
                              help="FREQUENCY is never used in the rate. This rule only rejects "
                                   "rows whose logged frequency is implausible.")
        freq_lo = st.number_input("Frequency minimum, Hz", min_value=0.0, max_value=100.0,
                                  value=float(current.freq_lo), step=1.0, disabled=not freq_gate)
        freq_hi = st.number_input("Frequency maximum, Hz", min_value=1.0, max_value=200.0,
                                  value=float(current.freq_hi), step=1.0, disabled=not freq_gate)
    with c3:
        st.markdown("**Steadiness and calibrated basis**")
        transient_gate = st.toggle("Apply the transient rule", value=bool(current.transient_gate),
                                   help="Off: every usable row counts as steady, so K is "
                                        "calibrated on whatever was happening at the test.")
        transient_cv = st.number_input("Transient CV limit, %", min_value=0.1, max_value=100.0,
                                       value=float(current.transient_cv * 100), step=0.5,
                                       disabled=not transient_gate)
        transient_window = st.number_input("Transient window, samples", min_value=2, max_value=96,
                                           value=int(current.transient_window), step=1,
                                           disabled=not transient_gate)
        elec_lo = st.number_input("Calibrated V window, low x", min_value=0.1, max_value=1.0,
                                  value=float(current.elec_basis_lo), step=0.05,
                                  help="A row carries a rate only while its voltage is inside "
                                       "[low x min, high x max] of the voltages at the well's "
                                       "calibration tests. Widening this admits rows on an "
                                       "electrical basis K was never calibrated on.")
        elec_hi = st.number_input("Calibrated V window, high x", min_value=1.0, max_value=5.0,
                                  value=float(current.elec_basis_hi), step=0.05)
    apply, reset = st.columns([1, 4])
    with apply:
        applied = st.form_submit_button("Apply rules", icon=":material/play_arrow:", type="primary")
    with reset:
        restored = st.form_submit_button("Restore defaults", icon=":material/restart_alt:")

if restored:
    set_rules(C.DEFAULT_THRESHOLDS)
    st.rerun()
if applied:
    set_rules(C.Thresholds(
        min_dp=float(min_dp), pip_lo=float(pip_lo), pip_hi=float(pip_hi),
        pdp_max=float(pdp_max),
        whp_over_pdp=bool(whp_over_pdp), pump_off_v=float(pump_off_v), pump_off_i=float(pump_off_i),
        freq_lo=float(freq_lo), freq_hi=float(freq_hi), freq_gate=bool(freq_gate),
        transient_window=int(transient_window), transient_cv=float(transient_cv) / 100,
        transient_gate=bool(transient_gate),
        elec_basis_lo=float(elec_lo), elec_basis_hi=float(elec_hi)))
    st.rerun()

# ---------------------------------------------------------------- effect against the defaults
st.subheader("Effect against the default rules", anchor=False)
if current.is_default:
    st.info("The rules are the defaults, so there is nothing to compare. Change one above and this "
            "section shows what it did to the row counts, to K and to the error against the well "
            "tests.", icon=":material/balance:")
else:
    st.dataframe(pd.DataFrame(current.changes(), columns=["rule", "default", "now"]),
                 hide_index=True,
                 column_config={"rule": "Rule", "default": "Default", "now": "Now"})
    base = D.get_results(scope.with_defaults())

    def headline(r) -> dict:
        d = r.rt
        mo = r.mape_overall.set_index("method")
        out = dict(usable_pct=float(d["usable"].mean() * 100),
                   steady_pct=float(d["steady"].mean() * 100),
                   rate_rows=int(d["rate_steady"].sum()),
                   matched=int(len(r.matched)),
                   suspect=int(r.matched["suspect"].sum()) if len(r.matched) else 0,
                   wells=len(r.wells_analysed))
        for m in ("M1_LOO", "M2_WALK", "BASE_LAST_TEST"):
            out[m] = float(mo.loc[m, "MAPE_all"]) if m in mo.index else float("nan")
        return out

    a, b = headline(base), headline(res)
    count = lambda v: f"{v:,.0f}"        # noqa: E731
    pct = lambda v: f"{v:.1f}"           # noqa: E731
    labels = [("rate_rows", "Rows carrying a rate", count), ("steady_pct", "Steady rows, %", pct),
              ("matched", "Matched tests", count), ("suspect", "Suspect tests", count),
              ("M1_LOO", f"MAPE {METHOD_LABELS['M1_LOO']}, %", pct),
              ("M2_WALK", f"MAPE {METHOD_LABELS['M2_WALK']}, %", pct)]
    cols = st.columns(3)
    for i, (k, lab, fmt) in enumerate(labels):
        with cols[i % 3]:
            delta = b[k] - a[k]
            # more rows is good news, a bigger error or one more suspect test is not
            worse = k.startswith("M") or k == "suspect"
            st.metric(lab, fmt(b[k]),
                      (("+" if delta > 0 else "-") + fmt(abs(delta))) if delta else "unchanged",
                      delta_color=("inverse" if worse else "normal") if delta else "off",
                      delta_arrow="auto" if delta else "off",
                      border=True, help=f"The default rules give {fmt(a[k])}.")
    st.caption("Error is the mean absolute % error against the matched well tests. A rule change "
               "that admits more rows but raises the error is admitting rows the method cannot "
               "explain - usually a stuck or pegged gauge.")

    kc = (base.cal[["WELL_NAME", "regime", "K_single", "n_tests"]]
          .merge(res.cal[["WELL_NAME", "regime", "K_single", "n_tests"]],
                 on=["WELL_NAME", "regime"], how="outer", suffixes=("_default", "_now")))
    kc["change_pct"] = (kc["K_single_now"] / kc["K_single_default"] - 1) * 100
    st.markdown("**Calibration factor per well**")
    st.dataframe(kc, hide_index=True, column_config={
        "WELL_NAME": "Well", "regime": "Regime",
        "K_single_default": st.column_config.NumberColumn("K default", format="%.2f"),
        "K_single_now": st.column_config.NumberColumn("K now", format="%.2f"),
        "n_tests_default": st.column_config.NumberColumn("Tests default"),
        "n_tests_now": st.column_config.NumberColumn("Tests now"),
        "change_pct": st.column_config.NumberColumn("Change %", format="%+.1f")})

# ---------------------------------------------------------------- the dP evidence
st.subheader("The dP floor, measured", anchor=False)
st.markdown("""
The 300 psi floor on `dP = PDP - PIP` is the one rule that was picked as a round number rather than
derived, so it is worth knowing what it actually does. Measured on both fields, with the floor at
300 and at 0:

| | dP > 300 (default) | no dP floor |
|---|---|---|
| **GC31** rows carrying a rate | 50,041 | 50,041 |
| **GC31** K per well | 119.27 / 13.17 / 22.14 | 119.27 / 13.17 / 22.14 |
| **GC31** MAPE M1 / M2 | 8.4 % / 10.6 % | 8.4 % / 10.6 % |
| **Meleiha** rows carrying a rate | 244,450 | 246,930 |
| **Meleiha** matched tests (suspect) | 48 (5) | 49 (6) |
| **Meleiha** MAPE M1 / M2 | 14.5 % / 18.4 % | 24.3 % / 30.8 % |

On **GC31 the rule does nothing**: every row it rejects is already rejected for a missing pressure
or a stopped pump, so removing it changes no K, no rate and no error.

On **Meleiha it is doing real work**. Removing it admits 2,480 rows on M-80 ST, every one of them at
a `dP` of exactly 199.4 psi - a frozen reading, not a measurement. Those rows drag in one more well
test, which the robust-z screen then marks suspect, and the error against the well tests nearly
doubles: 14.5 % to 24.3 % on M1, 18.4 % to 30.8 % on M2.

So the floor is kept as the default, and made adjustable here instead of removed: on a field where
it does nothing you can set it to 0 and confirm that for yourself, and on a field where it is
catching a stuck gauge the cost of removing it is on screen rather than hidden.
""")

st.subheader("The PDP ceiling, and when to set one", anchor=False)
st.markdown("""
There is deliberately **no PDP ceiling by default**: discharge pressure spans too wide a range
between fields to bound sensibly, and a bad PDP normally fails the dP rule or the PIP range anyway.

A gauge **pegged at its rail** is the case that gets through. On Meleiha, M-80 ST reports
`PDP = 6554 psi` for 56,835 rows - a 16-bit limit, not a measurement - and because PIP moves
underneath it the resulting `dP` of 4,700-5,800 psi looks entirely reasonable. 49,068 of those rows
carry a virtual rate today. Setting the ceiling to 6,000 psi removes them, and the error against the
well tests *falls* from 14.5 % to 12.7 % on M1.

It is offered rather than imposed because the right value is a property of the field's
instrumentation, not of the method. SWM A-2-2 reaches 53,947 psi, so a single number would not
serve both wells.
""")
