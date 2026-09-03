#!/usr/bin/env python3
"""Glucose outcomes across two sequential changes on the developer's own device.

Two settings changed at different times in the past 28 days, which makes them separable:

  2026-08-26 09:00 local  ISF stopped moving with glucose. variable_sens and dynamic_isf
                          became identical to three decimal places and the within-day spread
                          fell from sd 20-60 to sd 3-5 mg/dL/U, leaving the profile value
                          modulated only by autosens (61.2, and 61.2/1.2 = 51.0).
  2026-08-31 19:00 local  Loop cadence went from 5 minutes to 1 minute. Median inter-cycle
                          gap fell from ~280 s to ~60 s inside one hour.

So arm A against arm B isolates the ISF change with cadence held at 5 minutes, and arm B
against arm C isolates the cadence change with ISF already static.

The comparison is sequential and observational. There is no counterfactual trajectory, so
anything that drifted over the same weeks is confounded with the change. Both boundaries were
measured from the data rather than taken from memory.

Time weighting is the one control that matters mechanically. A 1-minute arm emits five times
the rows per hour, so counting cycles in range would weight arm C five-fold against the others.
Every reading is therefore snapped to a common 5-minute grid, one value per bucket, before any
metric is computed.
"""
import argparse
import json
import os

import numpy as np
import pandas as pd
import psycopg2

DSN = "dbname=oref host=127.0.0.1 port=5432"
USER = "tim"

# Boundaries measured in this session; see the module docstring.
ISF_SWITCH = pd.Timestamp("2026-08-26 09:00", tz="Europe/London")
CADENCE_SWITCH = pd.Timestamp("2026-08-31 19:00", tz="Europe/London")

GRID = "5min"
N_BOOT = 10000
SEED = 20260903


def load(conn, start, end):
    q = """
      select ts_utc, cgm_mgdl
      from boost_decisions
      where user_id = %s and ts_utc >= %s and ts_utc < %s
        and cgm_mgdl is not null and cgm_mgdl between 20 and 500
      order by ts_utc
    """
    df = pd.read_sql(q, conn, params=(USER, start, end))
    df["ts"] = pd.to_datetime(df.ts_utc, utc=True).dt.tz_convert("Europe/London")
    return df.drop(columns=["ts_utc"])


def to_grid(df):
    """One glucose value per 5-minute bucket, so every arm is weighted by time not by cycles."""
    g = df.set_index("ts").resample(GRID).last().dropna(subset=["cgm_mgdl"])
    return g.reset_index()


def metrics(bg):
    bg = np.asarray(bg, dtype=float)
    if bg.size == 0:
        return {}
    return {
        "n_readings": int(bg.size),
        "hours": round(bg.size * 5 / 60, 1),
        "TIR_70_180": 100 * float(np.mean((bg >= 70) & (bg <= 180))),
        "TING_63_140": 100 * float(np.mean((bg >= 63) & (bg <= 140))),
        "TBR_lt70": 100 * float(np.mean(bg < 70)),
        "TBR_lt54": 100 * float(np.mean(bg < 54)),
        "TAR_gt180": 100 * float(np.mean(bg > 180)),
        "mean_mgdl": float(np.mean(bg)),
        "CV_pct": 100 * float(np.std(bg, ddof=1) / np.mean(bg)) if bg.size > 1 else float("nan"),
    }


def day_block_boot(g, stat, rng, n=N_BOOT):
    """Resample whole local days with replacement.

    A day is the block because glucose is strongly autocorrelated within a day and the diurnal
    shape is part of what a setting change acts on. It also means the effective sample size is
    the number of days, not the number of readings, which is the honest count here.
    """
    g = g.copy()
    g["day"] = g.ts.dt.floor("D")
    days = [d.cgm_mgdl.values for _, d in g.groupby("day")]
    if len(days) < 2:
        return (float("nan"), float("nan"), len(days))
    out = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(days), len(days))
        out[i] = stat(np.concatenate([days[j] for j in pick]))
    return (float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5)), len(days))


def smb_summary(conn, start, end, label):
    q = """
      select ts_utc, insulin, is_smb, event_type
      from boost_treatments
      where user_id = %s and ts_utc >= %s and ts_utc < %s
        and insulin is not null and insulin > 0
    """
    t = pd.read_sql(q, conn, params=(USER, start, end))
    hours = (end - start).total_seconds() / 3600
    smb = t[t.is_smb == True]
    man = t[(t.is_smb != True)]
    return {
        "arm": label,
        "hours": round(hours, 1),
        "smb_count": int(len(smb)),
        "smb_units": round(float(smb.insulin.sum()), 2),
        "smb_per_24h": round(len(smb) / hours * 24, 1),
        "smb_units_per_24h": round(float(smb.insulin.sum()) / hours * 24, 2),
        "smb_median_u": round(float(smb.insulin.median()), 3) if len(smb) else None,
        "manual_units_per_24h": round(float(man.insulin.sum()) / hours * 24, 2),
    }


def insulin_reconstruction(conn, start, end, profile_basal_u_hr=0.72):
    """Total delivery per 24 h, as temp basal integrated plus the SMB stream.

    Each temp basal runs until the next one starts or its stated duration expires, whichever
    comes first, which is how AAPS supersedes them. Uncovered time is reported rather than
    filled, because the fill needs a basal schedule this table does not carry; the second
    figure bounds it with a flat rate and is an upper bound on the correction, not a
    measurement. Validation: arm A reconstructs to 33.2 U/24 h against the engine's own
    tdd_24h median of 33.7, which is the check that this is integrating the right thing.
    """
    q = """
      select ts_utc, event_type, rate, duration, insulin, is_smb
      from boost_treatments where user_id = %s and ts_utc >= %s and ts_utc < %s
      order by ts_utc
    """
    t = pd.read_sql(q, conn, params=(USER, start - pd.Timedelta(hours=6), end))
    t["ts"] = pd.to_datetime(t.ts_utc, utc=True).dt.tz_convert("Europe/London")
    tb = t[(t.event_type == "Temp Basal") & t.rate.notna()].sort_values("ts").copy()
    tb["nxt"] = tb.ts.shift(-1)
    tb["end_dur"] = tb.ts + pd.to_timedelta(tb.duration.fillna(0), unit="m")
    tb["end"] = tb[["nxt", "end_dur"]].min(axis=1)
    tb.loc[tb["end"].isna(), "end"] = tb["end_dur"]
    tb = tb[(tb.ts >= start) & (tb.ts < end)]
    tb["hours"] = (tb["end"] - tb.ts).dt.total_seconds() / 3600
    hours = (end - start).total_seconds() / 3600
    cov = float(tb.hours.sum() / hours)
    basal = float((tb.rate * tb.hours).sum()) / hours * 24
    smb = float(t[(t.ts >= start) & (t.ts < end) & (t.is_smb == True)].insulin.sum()) / hours * 24
    fill = profile_basal_u_hr * max(0.0, 1 - cov) * 24
    return {"tempbasal_coverage_pct": round(100 * cov, 1),
            "basal_u_24h": round(basal, 2), "smb_u_24h": round(smb, 2),
            "total_u_24h": round(basal + smb, 2),
            "total_u_24h_gapfilled_upper": round(basal + smb + fill, 2)}


def cycles(conn, start, end):
    q = "select count(*) from boost_decisions where user_id=%s and ts_utc>=%s and ts_utc<%s"
    with conn.cursor() as cur:
        cur.execute(q, (USER, start, end))
        return int(cur.fetchone()[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "out"))
    a = ap.parse_args()

    conn = psycopg2.connect(DSN)
    now = pd.Timestamp.now(tz="Europe/London").floor("min")
    start = now - pd.Timedelta(days=a.days)

    arms = [
        ("A dynamic ISF, 5 min", start, ISF_SWITCH),
        ("B static ISF, 5 min", ISF_SWITCH, CADENCE_SWITCH),
        ("C static ISF, 1 min", CADENCE_SWITCH, now),
    ]

    rng = np.random.default_rng(SEED)
    rows, boots, smbs = [], {}, []
    grids = {}
    for label, s, e in arms:
        raw = load(conn, s, e)
        g = to_grid(raw)
        grids[label] = g
        m = metrics(g.cgm_mgdl.values)
        m["arm"] = label
        m["from"] = str(s)[:16]
        m["to"] = str(e)[:16]
        m["raw_cycles"] = cycles(conn, s, e)
        rows.append(m)
        boots[label] = {
            k: day_block_boot(g, f, rng)
            for k, f in {
                "TIR_70_180": lambda b: 100 * np.mean((b >= 70) & (b <= 180)),
                "TING_63_140": lambda b: 100 * np.mean((b >= 63) & (b <= 140)),
                "TBR_lt70": lambda b: 100 * np.mean(b < 70),
                "mean_mgdl": lambda b: float(np.mean(b)),
            }.items()
        }
        sm = smb_summary(conn, s, e, label)
        sm.update(insulin_reconstruction(conn, s, e))
        smbs.append(sm)

    # Paired-by-nothing difference: independent day-block bootstrap of each arm, differenced.
    def diff_ci(l1, l2, stat, n=N_BOOT):
        r = np.random.default_rng(SEED + 1)
        def draws(label):
            g = grids[label].copy()
            g["day"] = g.ts.dt.floor("D")
            days = [d.cgm_mgdl.values for _, d in g.groupby("day")]
            o = np.empty(n)
            for i in range(n):
                pick = r.integers(0, len(days), len(days))
                o[i] = stat(np.concatenate([days[j] for j in pick]))
            return o
        d = draws(l1) - draws(l2)
        return float(np.mean(d)), float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))

    comparisons = {}
    for name, l1, l2 in [
        ("ISF: static(B) minus dynamic(A), cadence held 5 min",
         "B static ISF, 5 min", "A dynamic ISF, 5 min"),
        ("Cadence: 1 min(C) minus 5 min(B), ISF held static",
         "C static ISF, 1 min", "B static ISF, 5 min"),
    ]:
        comparisons[name] = {
            k: diff_ci(l1, l2, f)
            for k, f in {
                "TIR_70_180": lambda b: 100 * np.mean((b >= 70) & (b <= 180)),
                "TING_63_140": lambda b: 100 * np.mean((b >= 63) & (b <= 140)),
                "TBR_lt70": lambda b: 100 * np.mean(b < 70),
                "mean_mgdl": lambda b: float(np.mean(b)),
            }.items()
        }

    os.makedirs(a.out, exist_ok=True)
    res = {"generated": str(now), "window_days": a.days,
           "isf_switch": str(ISF_SWITCH), "cadence_switch": str(CADENCE_SWITCH),
           "arms": rows, "per_arm_ci": {k: {kk: list(vv) for kk, vv in v.items()} for k, v in boots.items()},
           "comparisons": {k: {kk: list(vv) for kk, vv in v.items()} for k, v in comparisons.items()},
           "smb": smbs}
    with open(os.path.join(a.out, "isf_cadence_tir.json"), "w") as f:
        json.dump(res, f, indent=2)

    w = 22
    print(f"{'arm':<24}{'days':>6}{'hours':>7}{'TIR':>8}{'TING':>8}{'<70':>7}{'<54':>7}{'>180':>8}{'mean':>8}{'CV':>7}")
    for m in rows:
        nd = boots[m['arm']]['TIR_70_180'][2]
        print(f"{m['arm']:<24}{nd:>6}{m['hours']:>7.0f}{m['TIR_70_180']:>8.1f}{m['TING_63_140']:>8.1f}"
              f"{m['TBR_lt70']:>7.2f}{m['TBR_lt54']:>7.2f}{m['TAR_gt180']:>8.1f}{m['mean_mgdl']:>8.0f}{m['CV_pct']:>7.1f}")

    print("\nPer-arm 95% CI (day-block bootstrap, block = one local day)")
    for label in boots:
        b = boots[label]
        print(f"  {label:<24} TIR {b['TIR_70_180'][0]:5.1f} to {b['TIR_70_180'][1]:5.1f}   "
              f"TBR<70 {b['TBR_lt70'][0]:4.2f} to {b['TBR_lt70'][1]:4.2f}   (n days = {b['TIR_70_180'][2]})")

    print("\nDifferences, percentage points (95% CI). Interval spanning zero = not distinguishable.")
    for name, d in comparisons.items():
        print(f"  {name}")
        for k, (m, lo, hi) in d.items():
            verdict = "distinguishable" if (lo > 0 or hi < 0) else "NOT distinguishable"
            print(f"     {k:<14} {m:+7.2f}  [{lo:+7.2f}, {hi:+7.2f}]  {verdict}")

    print("\nInsulin delivery per 24 h")
    print(f"{'arm':<24}{'SMBs/24h':>9}{'med U':>7}{'basal':>8}{'SMB':>8}{'total':>8}{'gapfill':>9}{'TBcov%':>8}")
    for s in smbs:
        print(f"{s['arm']:<24}{s['smb_per_24h']:>9.1f}{str(s['smb_median_u']):>7}"
              f"{s['basal_u_24h']:>8.2f}{s['smb_u_24h']:>8.2f}{s['total_u_24h']:>8.2f}"
              f"{s['total_u_24h_gapfilled_upper']:>9.2f}{s['tempbasal_coverage_pct']:>8.1f}")

    print(f"\nwrote {os.path.join(a.out, 'isf_cadence_tir.json')}")


if __name__ == "__main__":
    main()
