# Static against dynamic ISF, and 5-minute against 1-minute cadence, on one device

Single participant, `self`, 28 days to 2026-09-03 17:06 local. Database refreshed to t=now before
the analysis. Scripts and outputs in `backtesting/scripts/2026-09-isf-cadence-tir/`.

## Two changes, and why they separate

Two settings changed on different dates, which is what makes them separable rather than confounded
with each other.

ISF stopped moving with glucose on 2026-08-26 at about 09:00 local. `variable_sens` and
`dynamic_isf` became identical to three decimal places within one hour, having differed by 5.7,
12.6 and 44.6 mg/dL/U in the three hours before, and the within-day spread of ISF fell from a
standard deviation of 20 to 60 down to 3 to 5. What is left is the profile value of 61.2 modulated
only by autosens, the recurring 51.0 being 61.2 divided by 1.2. The `running_dynamic_isf` flag
stays true throughout and does not mark this change, so it should not be used to find it. An
independent corroboration is that `tdd_24h` goes null from 2026-08-27, the dynamic-ISF machinery
having stopped computing the total daily dose it needs.

Loop cadence went from 5 minutes to 1 minute on 2026-08-31 at 19:00 local, the median inter-cycle
gap falling from about 280 s to about 60 s inside a single hour. That boundary is 70 hours before
the end of the window.

So arm A against arm B isolates the ISF change with cadence held at 5 minutes, and arm B against
arm C isolates the cadence change with ISF already static.

Every reading is snapped to a common 5-minute grid before any metric is computed. Without that, the
1-minute arm contributes five times the rows per hour and would be weighted five-fold.

## Glucose outcomes

| arm | days | hours | TIR 70-180 | TING 63-140 | <70 | <54 | >180 | mean mg/dL | CV % |
|---|---|---|---|---|---|---|---|---|---|
| A dynamic ISF, 5 min | 21 | 456 | 85.0 | 72.4 | 5.51 | 0.40 | 9.5 | 121 | 34.0 |
| B static ISF, 5 min | 6 | 125 | 81.2 | 66.6 | 3.53 | 0.53 | 15.3 | 129 | 38.8 |
| C static ISF, 1 min | 4 | 69 | 78.7 | 68.0 | 8.94 | 0.85 | 12.3 | 120 | 36.8 |

Differences in percentage points, with 95% intervals from a bootstrap that resamples whole local
days, so the effective sample size is the number of days rather than the number of readings.

| comparison | TIR | TING | below 70 | mean mg/dL |
|---|---|---|---|---|
| static minus dynamic, cadence held at 5 min | -3.83 (-11.24 to +4.31) | -5.86 (-14.90 to +2.83) | -1.94 (-4.81 to +1.03) | +7.42 (-2.23 to +18.52) |
| 1 min minus 5 min, ISF held static | -2.74 (-11.23 to +4.91) | +1.37 (-6.58 to +9.46) | +5.25 (+0.92 to +8.92) | -8.26 (-20.49 to +3.86) |

Static against dynamic ISF is unproven on every measure. Each interval spans zero comfortably, and
with six days in arm B the study cannot resolve a difference smaller than roughly eight percentage
points of time in range. Nothing here says the two are equivalent; it says this window cannot tell
them apart.

For the cadence change, time in range is also not distinguishable. Time below 70 mg/dL is: it rises
by 5.25 percentage points and the interval excludes zero. That is the one result in this report that
clears its own bar, and it is the one worth acting on.

## Insulin delivery

Reconstructed from the treatment stream, each temporary basal running until the next begins or its
stated duration expires. Arm A reconstructs to 33.2 U per 24 h against the engine's own `tdd_24h`
median of 33.7, which is the check that the integration is doing what it claims.

| arm | SMBs per 24 h | median SMB U | basal U/24h | SMB U/24h | total U/24h | temp basal coverage |
|---|---|---|---|---|---|---|
| A dynamic ISF, 5 min | 47.2 | 0.20 | 13.69 | 19.49 | 33.18 | 89.0% |
| B static ISF, 5 min | 55.6 | 0.20 | 13.20 | 19.48 | 32.68 | 90.2% |
| C static ISF, 1 min | 126.8 | 0.15 | 10.13 | 29.73 | 39.86 | 95.6% |

At 1-minute cadence the loop issues 2.3 times as many microboluses, each slightly smaller, and
delivers 53 per cent more insulin through the SMB path while cutting basal by 23 per cent. Total
delivery rises about 22 per cent, from 32.7 to 39.9 U per 24 h. Uncovered time is counted as zero
basal rather than filled, and since arm A and arm B have more uncovered time than arm C, filling it
would narrow the gap: at a flat 0.72 U/h the totals become 35.1, 34.4 and 40.6, an 18 per cent rise
rather than 22.

## What this does and does not establish

The rise in time below range is not one bad night. Per local day in arm C it runs 1.69, 11.19, 6.29
and 11.65 per cent, so three of the four days sit above 6 per cent. On 2026-09-01, 2.53 per cent of
the day was spent below 54 mg/dL, which is past the consensus absolute floor of 1 per cent that the
programme treats as a kill-switch criterion. Arm C's 8.94 per cent below 70 is also past the 4 per
cent floor, though it should be noted that arm A sits at 5.51 per cent and is already above it, so
the floor was not being met before the cadence change either.

The comparison is sequential and observational. There is no counterfactual glucose trajectory, so
anything that drifted across these weeks moves with the arm assignment and cannot be separated from
it. Carbohydrate is never recorded on this device, zero entries across all three arms, so intake is
unobserved rather than shown to be equal, and a change in eating would land entirely in the
unannounced-meal path where it would look like a dosing effect.

Arm C is 70 hours and four local days. A day-block bootstrap on four blocks is a weak instrument,
and the interval on time below 70 should be read as the weakest kind of positive result rather than
a settled one. The direction is corroborated by the delivery figures, which do not depend on the
bootstrap at all: 22 per cent more insulin per day is a large change to make without intending it,
and it is the most likely mechanism for the extra time below range.

Tier PROVISIONAL for the cadence result, since it is a single test on one participant with a wide
interval. Tier unproven for the ISF comparison, which is underpowered rather than negative.

## What follows

The 1-minute arm is delivering about a fifth more insulin per day than the 5-minute arm on the same
settings, and time below 70 mg/dL has roughly doubled. Whether the intended change was the cadence
alone or the dose response that follows from it is a question for the person running it. If the
cadence is to continue, the caps that bound cumulative SMB volume are the levers that would hold
delivery near where it was, and they would need re-placing for a loop that now has five times as
many opportunities to dose.
