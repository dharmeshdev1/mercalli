# 3. Recovery forecasts are intervals with calibrated coverage

Status: accepted

## Context

How long an incident will last is highly uncertain: the same opening message can precede a ten
minute blip or a ten hour outage. A single number would be wrong most of the time and would hide
that uncertainty from people deciding whether to fail over, page someone, or wait.

## Decision

Predict the 10th, 50th and 90th percentiles of the remaining time with gradient boosted quantile
regression on the log of the remaining minutes, then widen the interval with split conformal
calibration (conformalized quantile regression, Romano, Patterson and Candes, NeurIPS 2019) on a
held out period that comes after the training period.

## Consequences

- The 80% interval keeps its coverage on new data as long as the future resembles the calibration
  period, which is measured, not assumed: test coverage is reported in the model card, and live
  coverage is measured on forecasts as they were made.
- Intervals are wide. That is the honest answer, and the median still beats every baseline.
- Quantile models can cross; the median is clipped into the interval.
