# Contributing

Thanks for helping. Mercalli is easiest to improve in three places:

- **Coverage.** Add a service to `catalog/seed.yaml` and run `mercalli catalog discover --top 0` to
  verify it. If its status page runs software Mercalli cannot read yet, an adapter in
  `src/mercalli/sources/` is a small, self-contained change.
- **Models.** `src/mercalli/ml/` holds the recovery forecaster and the blast radius model. Any change
  should report its effect on the test period with `mercalli train`, against the same baselines.
- **The web app.** `web/` is a Next.js static export. `pnpm dev` runs it against the files in
  `web/public/api`, which `mercalli tick --publish web/public` writes from live data.

## Setup

```sh
uv sync --all-extras --group dev
uv run pytest
uv run ruff check . && uv run ruff format --check .
cd web && pnpm install && pnpm test && pnpm build
```

## Ground rules

- Every number in documentation comes from a run you can reproduce. Say how.
- Features in a model use only information available at prediction time. If you are unsure,
  write a test like the ones in `tests/test_ml.py` that proves it.
- Be gentle with status pages. They exist for their customers. Keep request rates where they are.
