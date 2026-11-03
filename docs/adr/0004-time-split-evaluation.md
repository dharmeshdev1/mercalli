# 4. Evaluate on the future, with features from the past

Status: accepted

## Context

Incident data is a time series of correlated events. A random split leaks: snapshots of one incident
land in both train and test, and an outage that hits forty services at once teaches the model about
the test set.

## Decision

- Split by incident start time into train, calibration and test periods. Nothing from the test
  period is used to fit, tune or calibrate.
- Build every feature only from information available at the moment of the prediction. Recovery
  features come from updates posted so far. Blast radius counts (co-failures, mentions, failure
  rates) are exclusive cumulative counts over incidents that began before the example's time.
- Compare against simple baselines given the same information.
- After evaluation, retrain with the same procedure on all data for production, so the deployed
  models learn from the most recent incidents.

## Consequences

- Reported numbers are what a user would have seen had the model been running then.
- The deployed model is not the evaluated model, but the evaluated procedure. Live accuracy, measured
  on the deployed model's own forecasts, closes that gap over time.
