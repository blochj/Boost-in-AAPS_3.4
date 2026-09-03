#!/usr/bin/env python3
"""Would a 3-minute floor on microbolus spacing hold delivery down, and for what reason?

Two separate questions, answered separately because they have different answers.

The first is whether the 1-minute glucose trace is too noisy to dose on, which is the usual
argument for spacing. It is measured here as the share of consecutive steps that change sign at
each sampling interval. A noise-dominated series reverses about half the time.

The second is how much of this participant's microbolus stream a 3-minute floor would touch. That
is a bound rather than a simulation: removing a dose lowers insulin on board, so the loop would
re-dose part of what was withheld, and there is no glucodynamic model here to say how much. The
trial arms give the empirical answer the bound cannot.
"""
import numpy as np
import pandas as pd
import psycopg2

DSN = "dbname=oref host=127.0.0.1 port=5432"
USER = "tim"
ISF_SWITCH = pd.Timestamp("2026-08-26 09:00", tz="Europe/London")
CADENCE_SWITCH = pd.Timestamp("2026-08-31 19:00", tz="Europe/London")


def main():
    c = psycopg2.connect(DSN)
    now = pd.Timestamp.now(tz="Europe/London")

    d = pd.read_sql(
        """select ts_utc, cgm_mgdl from boost_decisions where user_id=%s
           and ts_utc>=%s and ts_utc<%s and cgm_mgdl between 20 and 500 order by ts_utc""",
        c, params=(USER, CADENCE_SWITCH, now))
    d["ts"] = pd.to_datetime(d.ts_utc, utc=True).dt.tz_convert("Europe/London")
    d = d.set_index("ts")

    print("Trend reliability by sampling interval, 1-minute record")
    print(f"{'interval':>10}{'n':>7}{'median |step|':>15}{'reversals':>11}{'persistence':>13}")
    for m in (1, 2, 3, 5, 10):
        g = d.cgm_mgdl.resample(f"{m}min").last().dropna()
        dl = g.diff().dropna()
        if len(dl) < 20:
            continue
        rev = float((np.sign(dl).diff().abs() > 0).mean())
        per = float((np.sign(dl) == np.sign(dl.shift(1))).mean())
        print(f"{m:>8} min{len(dl):>7}{dl.abs().median():>15.1f}{100*rev:>10.0f}%{per:>13.2f}")
    print("A series carrying no trend information reverses ~50% of the time.\n")

    q = """select ts_utc, insulin from boost_treatments where user_id=%s
           and is_smb=true and insulin>0 and ts_utc>=%s and ts_utc<%s order by ts_utc"""
    print("How much of the microbolus stream a spacing floor would touch")
    for lbl, s, e in [("5 min arm", ISF_SWITCH, CADENCE_SWITCH), ("1 min arm", CADENCE_SWITCH, now)]:
        t = pd.read_sql(q, c, params=(USER, s, e))
        t["ts"] = pd.to_datetime(t.ts_utc, utc=True).dt.tz_convert("Europe/London")
        gap = t.ts.diff().dt.total_seconds() / 60
        h = (e - s).total_seconds() / 3600
        print(f"  {lbl}: {len(t)} SMBs, {t.insulin.sum():.1f} U over {h:.0f} h, "
              f"median gap {gap.median():.1f} min")
        for thr in (2, 3, 5):
            m = gap < thr
            print(f"     within {thr} min of the previous: {100*m.mean():4.1f}% of doses, "
                  f"{100*t.insulin[m].sum()/t.insulin.sum():4.1f}% of units "
                  f"({t.insulin[m].sum()/h*24:5.2f} U/24h)")
    print("\nThese are upper bounds on what a floor removes, not estimates of what it saves:")
    print("withheld insulin lowers IOB, so the loop re-doses part of it on the next slot.")


if __name__ == "__main__":
    main()
