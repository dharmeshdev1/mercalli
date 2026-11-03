# 2. Vendors' status pages are the ground truth

Status: accepted

## Context

An outage can be observed three ways: what the vendor says (status pages), what users report
(crowd reports, social media), and what probes measure. Each has gaps. Vendors post late, some never
post, and impact labels are the vendor's opinion. Crowd reports need a large audience and are
noisy. Probes from one vantage point see their own network problems.

## Decision

Use status pages as the primary signal and the training label. Every model predicts something the
vendor will eventually publish: when it will mark an incident resolved, and whether another vendor
will post an incident. Keep Mercalli's own observations (`first_seen_at`, inferred resolutions)
separate from the vendor's data.

## Consequences

- Labels are objective in the sense that they are recorded facts, but they inherit vendor behavior:
  a vendor that resolves slowly on its page trains the model to expect slow resolutions.
- Incidents that vendors never post are invisible. The census and dependency graph partly make up
  for this by showing exposure even without an incident.
- Retroactive incidents (posted after they ended) are excluded from recovery training, because the
  model can never see them while they are open.
