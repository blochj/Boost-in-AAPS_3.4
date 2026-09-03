#!/usr/bin/env python3
"""Does the 1-minute loop dose more at the same glucose, or only because conditions differed?

The 22 per cent rise in daily insulin at 1-minute cadence is compatible with two stories. Either
the loop is more aggressive for a given state, which would be a cadence effect, or it met states
that called for more insulin, which would not be. The arms are therefore matched on glucose and,
in the second pass, on glucose and trend together, and delivery is compared cell by cell.

Delivery is the SMB stream summed into the same 5-minute grid the outcome analysis uses, expressed
as U per hour so cells of different occupancy are comparable. Basal is added separately because a
loop that raises SMB while cutting basal has not necessarily raised the total.
"""
import os

import numpy as np
import pandas as pd
import psycopg2

DSN = "dbname=oref host=127.0.0.1 port=5432"
USER = "tim"
ISF_SWITCH = pd.Timestamp("2026-08-26 09:00", tz="Europe/London")
CADENCE_SWITCH = pd.Timestamp("2026-08-31 19:00", tz="Europe/London")

BG_BINS = [0, 70, 90, 110, 130, 160, 200, 1000]
BG_LAB = ["<70", "70-90", "90-110", "110-130", "130-160", "160-200", ">200"]
DELTA_BINS = [-100, -3, 1, 5, 100]
DELTA_LAB = ["falling", "flat", "rising", "fast rise"]


def grid(conn, start, end):
    d = pd.read_sql(
        """select ts_utc, cgm_mgdl from boost_decisions
           where user_id=%s and ts_utc>=%s and ts_utc<%s and cgm_mgdl between 20 and 500
           order by ts_utc""", conn, params=(USER, start, end))
    d["ts"] = pd.to_datetime(d.ts_utc, utc=True).dt.tz_convert("Europe/London")
    g = d.set_index("ts").resample("5min").last().dropna(subset=["cgm_mgdl"])

    t = pd.read_sql(
        """select ts_utc, insulin from boost_treatments
           where user_id=%s and ts_utc>=%s and ts_utc<%s and is_smb=true and insulin>0""",
        conn, params=(USER, start, end))
    if len(t):
        t["ts"] = pd.to_datetime(t.ts_utc, utc=True).dt.tz_convert("Europe/London")
        s = t.set_index("ts").resample("5min").insulin.sum()
    else:
        s = pd.Series(dtype=float)
    g["smb_u"] = s.reindex(g.index).fillna(0.0)
    # gs_delta stopped being populated at the same time as tdd_24h, so trend is computed from
    # the grid rather than read from a column that is null across both arms.
    g["gs_delta"] = g.cgm_mgdl.diff()
    return g.reset_index()


def table(g, by_delta=False):
    g = g.copy()
    g["bg_bin"] = pd.cut(g.cgm_mgdl, BG_BINS, labels=BG_LAB, right=False)
    keys = ["bg_bin"]
    if by_delta:
        g["d_bin"] = pd.cut(g.gs_delta, DELTA_BINS, labels=DELTA_LAB, right=False)
        keys.append("d_bin")
    r = g.groupby(keys, observed=True).agg(n=("smb_u", "size"), u_per_hr=("smb_u", lambda x: x.mean() * 12))
    return r


def boot_diff(gc, gb, bins, n=4000, seed=7):
    """Difference in U/hr within a bin, day-block bootstrapped in both arms."""
    rng = np.random.default_rng(seed)
    out = {}
    for b in bins:
        a = gc[gc.bg_bin == b]
        c = gb[gb.bg_bin == b]
        if len(a) < 20 or len(c) < 20:
            out[b] = None
            continue
        def draw(x):
            x = x.copy(); x["day"] = x.ts.dt.floor("D")
            days = [d.smb_u.values for _, d in x.groupby("day")]
            o = np.empty(n)
            for i in range(n):
                p = rng.integers(0, len(days), len(days))
                o[i] = np.concatenate([days[j] for j in p]).mean() * 12
            return o
        d = draw(a) - draw(c)
        out[b] = (float(d.mean()), float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5)))
    return out


def main():
    conn = psycopg2.connect(DSN)
    now = pd.Timestamp.now(tz="Europe/London").floor("min")
    gb = grid(conn, ISF_SWITCH, CADENCE_SWITCH)
    gc = grid(conn, CADENCE_SWITCH, now)
    for g in (gb, gc):
        g["bg_bin"] = pd.cut(g.cgm_mgdl, BG_BINS, labels=BG_LAB, right=False)

    tb, tc = table(gb), table(gc)
    print("SMB delivery matched on glucose, U per hour")
    print(f"{'glucose':>10}{'5min n':>9}{'5min U/hr':>11}{'1min n':>9}{'1min U/hr':>11}{'ratio':>8}")
    for b in BG_LAB:
        nb = int(tb.n.get(b, 0)); nc = int(tc.n.get(b, 0))
        ub = float(tb.u_per_hr.get(b, np.nan)); uc = float(tc.u_per_hr.get(b, np.nan))
        ratio = uc / ub if ub and ub == ub and ub > 0 else float("nan")
        print(f"{b:>10}{nb:>9}{ub:>11.3f}{nc:>9}{uc:>11.3f}{ratio:>8.2f}")

    print("\nTime spent in each glucose band, per cent of readings")
    print(f"{'glucose':>10}{'5min':>9}{'1min':>9}")
    for b in BG_LAB:
        print(f"{b:>10}{100*int(tb.n.get(b,0))/tb.n.sum():>9.1f}{100*int(tc.n.get(b,0))/tc.n.sum():>9.1f}")

    print("\nDifference in U/hr at matched glucose, 1 min minus 5 min (95% CI)")
    for b, v in boot_diff(gc, gb, BG_LAB).items():
        if v is None:
            print(f"  {b:>10}  too few readings")
        else:
            m, lo, hi = v
            verdict = "distinguishable" if (lo > 0 or hi < 0) else "not distinguishable"
            print(f"  {b:>10}  {m:+7.3f}  [{lo:+7.3f}, {hi:+7.3f}]  {verdict}")

    print("\nStandardised: 5-minute arm's time-in-band applied to the 1-minute arm's dosing rate")
    w = np.array([int(tb.n.get(b, 0)) for b in BG_LAB], dtype=float); w /= w.sum()
    rb = np.array([float(tb.u_per_hr.get(b, np.nan)) for b in BG_LAB])
    rc = np.array([float(tc.u_per_hr.get(b, np.nan)) for b in BG_LAB])
    ok = ~np.isnan(rb) & ~np.isnan(rc)
    print(f"  observed 5 min SMB   {np.nansum(w[ok]*rb[ok])*24:6.2f} U/24h")
    print(f"  1 min rates, 5 min mix {np.nansum(w[ok]*rc[ok])*24:6.2f} U/24h")
    print("  The gap between these two is the part not explained by where glucose sat.")

    print("\nWith trend held as well (U/hr, cells with >=20 readings in both)")
    tbd, tcd = table(gb, True), table(gc, True)
    print(f"{'glucose':>10}{'trend':>11}{'5min':>9}{'1min':>9}{'ratio':>8}")
    for b in BG_LAB:
        for dl in DELTA_LAB:
            k = (b, dl)
            if k in tbd.index and k in tcd.index:
                nb, ub = int(tbd.n[k]), float(tbd.u_per_hr[k])
                nc, uc = int(tcd.n[k]), float(tcd.u_per_hr[k])
                if nb >= 20 and nc >= 20 and ub > 0:
                    print(f"{b:>10}{dl:>11}{ub:>9.3f}{uc:>9.3f}{uc/ub:>8.2f}")


if __name__ == "__main__":
    main()
