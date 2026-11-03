# Methodology

How the models are built, trained and judged. The current numbers live in the model card
(`api/v1/model.json`, shown on the site's Model page), which the weekly training job rewrites.
Numbers quoted here are from the training run recorded at the bottom of this page.

## The data

Atlassian Statuspage, which most SaaS vendors use, keeps a page's whole incident history online.
`mercalli backfill` walks every page's quarterly history index back to the page's creation, and
fetches the full timeline (every update, its status, its time and the components it named) of each
incident since 2021, up to the 1,200 most recent per service, so a vendor that posts one incident per
mobile carrier cannot drown out the rest. AWS, Google Cloud, Slack and Heroku publish shorter feeds,
which are snapshotted daily. Raw responses are cached exactly as received, and the analysis tables are
rebuilt from the cache in full each time.

Two corrections matter more than they look:

- **Time zones.** History pages print times in each page's own zone (`EDT`, `CEST`, `GMT-03:00`).
  They are converted to UTC from the zone the page states, so incidents line up across vendors.
- **Maintenance.** History pages list scheduled maintenance alongside incidents. Maintenance has a
  planned length and is not a failure, so it is excluded from every model.

## Recovery forecaster: how long will this incident last?

**Target.** The minutes left until the vendor marks the incident resolved, predicted at the moment
of each vendor update. One incident yields one training example per update, and each incident's
examples are weighted to sum to one, so long incidents do not dominate.

**Features,** all known at the moment of the forecast:

- Time so far, time since the last update, the gap before the last update, how many updates there
  have been, the current phase (investigating, identified, monitoring), and whether the vendor has
  already said it identified the cause or is monitoring a fix.
- The worst component state announced so far and how many components are affected.
- The service's own history: its median incident length, how many incidents it has had and how often
  they ran past two hours, shrunk toward its category when it has little history. Training rows use
  out-of-fold history, so an incident never sees itself.
- When it started (hour of day, weekend), and the service's category.
- What the incident is about: 384-dimension sentence embeddings (all-MiniLM-L6-v2) of the title and
  the first update, reduced to 16 principal components each.

**Model.** Three gradient boosted regressors (scikit-learn's histogram gradient boosting) predict the
10th, 50th and 90th percentile of the log of the remaining minutes.

**Calibration.** Quantile models are often overconfident on new data. The interval is widened by
split conformal calibration (conformalized quantile regression, Romano, Patterson and Candes, 2019):
on a calibration period that comes after the training period, find the smallest widening that would
have covered 80% of outcomes, and apply it. This is what makes the published 80% range mean 80%.

## Blast radius: what else will this incident hit?

**Example.** An incident at an upstream provider (a cloud, edge network, DNS or hosting provider, an
identity or AI provider, or a widely used platform such as GitHub) paired with every other service
whose history covers that moment.

**Label.** Whether that other service posted an incident of its own that began between 45 minutes
before and 180 minutes after the upstream incident began. The window opens early because dependents
often notice and post before their provider does.

**Features,** all counted strictly before the upstream incident began:

- How often the two failed together before, against what chance would give from each one's
  failure rate (a smoothed log lift).
- How often the dependent's own incident reports named the provider, and how often it blames
  third parties in general.
- Whether the census found the dependent using the provider for DNS, its edge network or hosting,
  and whether the upstream incident's region matches where the dependent is hosted.
- The upstream incident's impact, both services' categories, and the hour.

**Model.** A gradient boosted classifier, with probabilities calibrated by isotonic regression on
the calibration period.

**Honest reading.** A high probability means "this service is likely to post an incident soon",
learned from shared infrastructure and history. It is a prediction about what another vendor will
publish, not a proof of cause.

## Evaluation

- **Time split.** Train on incidents that began before the calibration period, calibrate on the
  next period, and report on the final period only. Nothing from the test period is used to fit,
  tune or calibrate. A random split would leak, because one outage touches many services at once.
- **Baselines.** Every model is compared with simple rules given the same information: for recovery,
  the median remaining time for the current phase, the same by phase and elapsed time, and the
  service's own median length minus time elapsed; for blast radius, ranking by base failure rate,
  by co-failure lift alone, by naming alone, and by the census alone.
- **Production.** After evaluation, both models are refitted with the same procedure on all data, so
  the deployed models learn from the most recent incidents.
- **Live accuracy.** Every forecast the running model makes is stored. Once the vendor resolves the
  incident, the forecast is scored, and the result is added to the model card each week.

## Intensity

A one number summary of how disrupted things are right now. The score adds up live incidents
weighted by impact (critical 4, major 2, minor 1), doubling incidents at providers that 20 or more
watched services depend on. Incidents open for more than a week are left out: those are vendors that
forgot to close them. The same score is replayed hour by hour over the past year, and its
percentiles become the levels: level IV means the internet is more disrupted than in 75% of hours
of the past year, and level X more than in 99.95%. The names follow the Mercalli scale.

## Limits

- The recovery model reads an incident's title as it was finally published. Vendors rarely rename
  incidents, but when they do, training saw a slightly better title than the live model will.

- Labels are vendors' own records. Vendors post late, some never post, and their impact labels are
  their opinion.
- Incidents posted only after they ended (about 6% of incidents with a full timeline) cannot be
  forecast while open, and are excluded from recovery training.
- The census and the regions it finds are a snapshot from one vantage point.

## Run recorded here

Trained on 3 November 2026. Training period: incidents that began before 1 July 2025. Calibration:
1 July to 31 December 2025. Test: 1 January to 3 November 2026.

| | Recovery forecaster | Blast radius |
| --- | --- | --- |
| Training examples | 65,525 snapshots of 26,547 incidents | 8,953,759 pairs, 344 positive |
| Calibration | 11,720 snapshots of 4,589 incidents | 1,119,352 pairs, 33 positive |
| Test | 30,131 snapshots of 11,390 incidents | 2,017,128 pairs, 189 positive, 86 upstream incidents |

Recovery, on every test snapshot: mean absolute log error 1.257 (best baseline 1.354), within a
factor of two of the truth 37.1% (best baseline 35.4%), 80% interval coverage 80.4% (78.9% at the
first update), median ratio between the interval's ends 56. The quantile models alone covered 72.4%;
conformal calibration widened the interval by 0.34 in log space to reach 80.4%.

Blast radius, per upstream incident with at least one attributed knock-on: recall at 10 of 0.613
(95% interval 0.52 to 0.71 from resampling incidents), at least one correct in the top 10 for 73.3%
(0.64 to 0.83), against 0.594 and 70.9% for the best single rule (services that had named the
provider before). For major and critical upstream incidents: recall at 10 of 0.692, at least one
correct for 85.7%.

What did not work, kept here because it shaped the design:

- **Labelling any incident in the window as a knock-on.** A model trained that way did no better than
  ranking services by how often they fail at all (average precision 0.132 against 0.134). Chatty
  vendors post incidents constantly, so the label measured them, not cascades.
- **A large gradient boosted model for blast radius.** With a few hundred positives a year it lost
  to the rule it was meant to beat. A small, heavily regularized model on evidence features won.
- **A leak caught in review.** An earlier version counted past co-failures from upstream incidents
  whose three hour windows overlapped the current one, so a dependent's incident could be both the
  label and part of a feature. Counts now use only windows that had closed, and a dependent's
  reports only count once they are a day old. The fix lowered recall at 10 from 0.625 to 0.613; the
  numbers above are after the fix.
- **Similar past incidents as a recovery feature.** Helpful in principle and interpretable, but worth
  only about 0.005 in log error. The latest vendor update's text was worth more (about 0.03).
