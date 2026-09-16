"""
ESP Virtual Rate - electrical power method (Camilleri power-equilibrium)
Wells: the 4 wells present in real-time SCADA (GC31).
  dP*Qp/(58847*eta_p) = sqrt(3)*Vm*I*PF*eta_m/746
  -> Qp = K * sqrt(3)*V*I/dP ,  K lumps eta_p*PF*eta_m*58847/746, transformer/cable ratios, Bo (surface calib)
"""
import pandas as pd, numpy as np, sys
RT = sys.argv[1] if len(sys.argv)>1 else 'data/AI_VW_REAL_TIME_DATA_Sample_Date_13-Sep-2026 V 1.1.xlsx'
WT = sys.argv[2] if len(sys.argv)>2 else 'data/GC31_DIGIWELLS_81_PARAM_MASTER_DATASET.csv'
OUT = sys.argv[3] if len(sys.argv)>3 else 'out'
WELLS_ALL = ['SA-0162_T','SA-0500_T','SA-0512H_T','SA-0991H_T']
EXCLUDED = {'SA-0991H_T': 'Real-time data cover only Apr-May 2024 (previous pump run) and match a single well test; '
                          'K cannot be validated, no leave-one-out, no PHI trend. New WG-4000 run installed Jun-2026 has no SCADA yet.'}
WELLS = [w for w in WELLS_ALL if w not in EXCLUDED]
WIN_H = 12          # +/- hours around test timestamp for mapping
MIN_ROWS = 6        # min steady 30-min rows in window

def load_rt(p):
    d = pd.read_pickle(p) if p.endswith('.pkl') else pd.read_excel(p)
    d = d[d.WELL_NAME.isin(WELLS)].copy()
    d = d.sort_values(['WELL_NAME','TIME_STAMP'])
    return d

def filter_rt(d):
    """Flag each row; keep rows usable for the power equation."""
    f = pd.DataFrame(index=d.index)
    f['missing_elec'] = d[['VOLTAGE','AMPERAGE']].isna().any(axis=1)
    f['missing_press'] = d[['PIP','PDP']].isna().any(axis=1)
    f['pump_off'] = (d.VOLTAGE < 100) | (d.AMPERAGE < 5) | (d.FREQUENCY == 0)
    dP = d.PDP - d.PIP
    f['bad_dP'] = ~(dP > 300)                       # pump not developing head / gauge fault
    f['bad_press_range'] = ~d.PIP.between(50, 5000) | ~d.PDP.between(300, 6000) | (d.WHP > d.PDP)
    f['bad_freq'] = d.FREQUENCY.notna() & ~d.FREQUENCY.between(30, 70) & (d.FREQUENCY != 0)
    # electrical basis: some tags report LV (VSD side, <800 V) others MV (motor side)
    d['V_BASIS'] = np.where(d.VOLTAGE < 800, 'LV', 'MV')
    # transient filter: rolling (6 samples) relative change of amps and dP
    g = d.groupby('WELL_NAME')
    amp_cv = g.AMPERAGE.transform(lambda s: s.rolling(6, min_periods=3).std() / s.rolling(6, min_periods=3).mean())
    dp_cv = (dP.groupby(d.WELL_NAME).transform(lambda s: s.rolling(6, min_periods=3).std()) /
             dP.groupby(d.WELL_NAME).transform(lambda s: s.rolling(6, min_periods=3).mean()).abs())
    f['transient'] = (amp_cv > 0.05) | (dp_cv > 0.05)
    d['dP'] = dP
    d['P_elec_kVA'] = np.sqrt(3) * d.VOLTAGE * d.AMPERAGE / 1000
    d['X'] = np.sqrt(3) * d.VOLTAGE * d.AMPERAGE / dP      # power-eq regressor
    d['usable'] = ~(f.missing_elec | f.missing_press | f.pump_off | f.bad_dP | f.bad_press_range | f.bad_freq)
    d['steady'] = d.usable & ~f.transient
    # frequency is ~50% missing; power method does not need it. Impute from V/Hz for info only.
    vhz = (d.VOLTAGE / d.FREQUENCY).where(d.FREQUENCY > 0).groupby([d.WELL_NAME, d.V_BASIS]).transform('median')
    d['FREQ_FILLED'] = d.FREQUENCY.where(d.FREQUENCY > 0, d.VOLTAGE / vhz)
    return d, f

def load_tests(p):
    t = pd.read_pickle(p) if p.endswith('.pkl') else pd.read_csv(p, low_memory=False)
    t = t[t.WELL_NAME.isin(WELLS)].copy()
    t['TEST_TS'] = pd.to_datetime(t['Test Timestamp'])
    num = lambda c: pd.to_numeric(t[c], errors='coerce')
    t = pd.DataFrame({'WELL_NAME': t.WELL_NAME, 'TEST_TS': t.TEST_TS,
        'Q_LIQ': num('P26: Liquid Rate / BFPD'), 'Q_OIL': num('P27: Oil Rtae /BOPD'), 'WC': num('P29: W.C %'),
        'GOR': num('P30: GOR / SCF/STB'), 'T_FREQ': num('P34: Mtr. Freq. /hz'), 'T_WHP': num('P35: WHP Psi'),
        'T_PIP': num('P39: P.Intake Pressure /psi'), 'T_PDP': num('P40: P. Discharge Pressure /psi'),
        'PUMP': t['P11: Pump type'], 'STAGES': num('P13: nr. Of Stages'), 'PUMP_DEPTH': num('P64: pump intake /TVD'),
        'SG': num('P55: Fluid desity ppg') / 8.33,
        'VALID': t['P81: Well test validiation']})
    return t.sort_values(['WELL_NAME','TEST_TS']).reset_index(drop=True)

def map_tests(d, t):
    rows = []
    for i, r in t.iterrows():
        g = d[(d.WELL_NAME == r.WELL_NAME)]
        for win in (WIN_H, 2*WIN_H):          # try +/-12 h, fall back to +/-24 h
            w = g[(g.TIME_STAMP >= r.TEST_TS - pd.Timedelta(hours=win)) & (g.TIME_STAMP <= r.TEST_TS + pd.Timedelta(hours=win))]
            s = w[w.steady]
            if len(s) >= MIN_ROWS: break
        near = (g.TIME_STAMP - r.TEST_TS).abs().min() if len(g) else pd.NaT
        rec = dict(r)
        rec.update(window_h=win, n_window=len(w), n_steady=len(s), nearest_rt_h=(near.total_seconds()/3600 if pd.notna(near) else np.nan))
        if len(s) >= MIN_ROWS:
            for c in ['VOLTAGE','AMPERAGE','FREQ_FILLED','PIP','PDP','WHP','dP','X','P_elec_kVA','MT']:
                rec['RT_'+c] = s[c].median()
            rec['V_BASIS'] = s.V_BASIS.mode()[0]
            rec['PIP_diff_vs_test'] = rec['RT_PIP'] - r.T_PIP if r.T_PIP > 0 else np.nan
            rec['MATCH'] = 'MATCHED'
            rec['RT_SG_MIX'] = (rec['RT_PDP'] - rec['RT_WHP']) / (0.433 * r.PUMP_DEPTH)   # tubing gradient (Camilleri App. B, friction ignored)
        else:
            rec['MATCH'] = 'NO_RT_DATA' if len(w) == 0 else 'INSUFFICIENT_STEADY_DATA'
        rows.append(rec)
    m = pd.DataFrame(rows)
    m['K'] = m.Q_LIQ / m.RT_X
    return m

def calibrate_validate(m):
    mm = m[m.MATCH == 'MATCHED'].copy()
    # outlier screen on K (robust z within well) - tests inconsistent with physics
    med = mm.groupby('WELL_NAME').K.transform('median')
    mad = mm.groupby('WELL_NAME').K.transform(lambda s: (s - s.median()).abs().median()) * 1.4826
    mm['K_outlier'] = ((mm.K - med).abs() / mad.replace(0, np.nan) > 3.5).fillna(False)
    res = []
    for w, g in mm.groupby('WELL_NAME'):
        g = g.sort_values('TEST_TS')
        for i, r in g.iterrows():
            others = g[(g.index != i) & ~g.K_outlier]
            prev = g[(g.TEST_TS < r.TEST_TS) & ~g.K_outlier]
            prevall = m[(m.WELL_NAME == w) & (m.TEST_TS < r.TEST_TS)]
            out = dict(WELL_NAME=w, TEST_TS=r.TEST_TS, Q_TEST=r.Q_LIQ, K_outlier=r.K_outlier)
            # M1: single K per well, leave-one-out
            out['Q_M1_LOO'] = others.K.median() * r.RT_X if len(others) else np.nan
            # M2: walk-forward, K from most recent previous test (field-realistic recalibration)
            out['Q_M2_WALK'] = prev.K.iloc[-1] * r.RT_X if len(prev) else np.nan
            # Baseline: last well test carried forward (what engineers use today)
            out['Q_BASE_LAST_TEST'] = prevall.Q_LIQ.iloc[-1] if len(prevall) else np.nan
            res.append(out)
    v = pd.DataFrame(res)
    for c in ['Q_M1_LOO','Q_M2_WALK','Q_BASE_LAST_TEST']:
        v['APE_'+c] = (v[c] - v.Q_TEST).abs() / v.Q_TEST * 100
    cal = mm[~mm.K_outlier].groupby('WELL_NAME').agg(K_median=('K','median'), K_cv_pct=('K', lambda s: s.std()/s.mean()*100),
                                                     n_tests=('K','size'), first=('TEST_TS','min'), last=('TEST_TS','max')).reset_index()
    return mm, v, cal

def virtual_rate(d, mm, cal):
    """Continuous rate: piecewise K (interpolated in time between calibration tests), plus single-K series."""
    out = []
    for w, g in d[d.steady].groupby('WELL_NAME'):
        g = g.copy()
        c = mm[(mm.WELL_NAME == w) & ~mm.K_outlier].sort_values('TEST_TS')
        if c.empty: continue
        # electrical-basis guard: K is only valid for the voltage level it was calibrated on
        # (tags switch between LV VSD-side and MV motor-side; transformer ratio unknown)
        vlo, vhi = 0.8 * c.RT_VOLTAGE.min(), 1.2 * c.RT_VOLTAGE.max()
        g['ELEC_BASIS_OK'] = g.VOLTAGE.between(vlo, vhi)
        g = g[g.ELEC_BASIS_OK].copy()
        g['K_single'] = c.K.median()
        tnum = g.TIME_STAMP.astype('int64'); cnum = c.TEST_TS.astype('int64')
        g['K_interp'] = np.interp(tnum, cnum, c.K)            # flat before first / after last test
        g['Q_VR_single'] = g.K_single * g.X
        g['Q_VR_interp'] = g.K_interp * g.X
        # PHI-style drift indicator: dP / electrical power, normalised to calibration median
        g['PHI_proxy'] = (g.dP / g.P_elec_kVA) / np.median(c.RT_dP / c.RT_P_elec_kVA)
        out.append(g)
    vr = pd.concat(out)
    daily = (vr.set_index('TIME_STAMP').groupby('WELL_NAME')[['Q_VR_single','Q_VR_interp','PHI_proxy','VOLTAGE','AMPERAGE','dP','PIP','PDP','WHP']]
             .resample('D').median().reset_index())
    return vr, daily

if __name__ == '__main__':
    d = load_rt(RT); d, flags = filter_rt(d)
    t = load_tests(WT)
    m = map_tests(d, t)
    mm, v, cal = calibrate_validate(m)
    vr, daily = virtual_rate(d, mm, cal)
    fs = pd.concat([d.WELL_NAME, flags], axis=1).groupby('WELL_NAME').sum()
    fs.insert(0, 'rows', d.groupby('WELL_NAME').size()); fs['usable'] = d.groupby('WELL_NAME').usable.sum(); fs['steady'] = d.groupby('WELL_NAME').steady.sum()
    vb = d.merge(vr[['ELEC_BASIS_OK']], left_index=True, right_index=True, how='left')
    fs['calibrated_basis_rows'] = vr.groupby('WELL_NAME').size()
    fs['usable_pct'] = (fs.usable / fs.rows * 100).round(1)
    import pickle; pickle.dump(dict(d=d, flags=fs, t=t, m=m, mm=mm, v=v, cal=cal, vr=vr, daily=daily), open(f'{OUT}/results.pkl', 'wb'))
    print(fs.to_string()); print(m[['WELL_NAME','TEST_TS','Q_LIQ','MATCH','n_steady','nearest_rt_h','V_BASIS','RT_X','K','PIP_diff_vs_test']].to_string())
    print(cal.to_string()); print(v.round(1).to_string())
    print('MAPE:', v[[c for c in v.columns if c.startswith('APE_')]].mean().round(1).to_dict())
    print('MAPE excl outliers:', v[~v.K_outlier][[c for c in v.columns if c.startswith('APE_')]].mean().round(1).to_dict())
