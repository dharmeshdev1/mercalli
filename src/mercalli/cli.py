"""The `mercalli` command."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mercalli import __version__

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="Outage intelligence for the services you depend on."
)
catalog_app = typer.Typer(no_args_is_help=True, help="Find and verify status pages.")
app.add_typer(catalog_app, name="catalog")
console = Console(stderr=True)

DEFAULT_DB = os.environ.get("MERCALLI_DB", "sqlite:///mercalli.db")
DEFAULT_API = os.environ.get("MERCALLI_API", "https://dharmeshdev1.github.io/mercalli/api/v1")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[bool, typer.Option("--version", help="Print the version and exit.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Log progress.")] = False,
) -> None:
    _setup_logging(verbose)
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@catalog_app.command("discover")
def catalog_discover(
    top: Annotated[int, typer.Option(help="Also search this many top domains for status pages. 0 to skip.")] = 20000,
    cache: Annotated[Path | None, typer.Option(help="Where to keep the downloaded domain ranking.")] = None,
) -> None:
    """Verify every seed entry and look for new status pages, then rewrite catalog/services.yaml."""
    from mercalli.catalog import load_seed, load_services, write_services
    from mercalli.discover import discover_from_domains, load_tranco, verify_seed
    from mercalli.http import PoliteClient

    async def run() -> None:
        async with PoliteClient(concurrency=48, per_host=2, timeout=8, retries=1) as c:
            services, failures = await verify_seed(load_seed(), c)
            console.print(f"verified {len(services)} seed entries, {len(failures)} could not be verified")
            found = []
            if top:
                tranco = await load_tranco(c, top, cache=cache)
                async with PoliteClient(concurrency=96, per_host=2, timeout=6, retries=0) as wide:
                    found = await discover_from_domains(wide, tranco, services)
                console.print(f"found {len(found)} more status pages among the top {top} domains")
            else:
                # Keep pages found automatically on earlier runs.
                found = [s for s in load_services() if not s.curated]
        write_services(services + found)
        for sid, why in sorted(failures.items()):
            console.print(f"  [dim]{sid}: {why}[/dim]")

    asyncio.run(run())


@catalog_app.command("export-services")
def catalog_export_services(
    out: Annotated[Path, typer.Option(help="Where to write services.json.")],
) -> None:
    """Write services.json from the catalog alone, for building the web app without a database."""
    import orjson

    from mercalli.api import ApiBuilder
    from mercalli.catalog import load_services
    from mercalli.store import Database

    db = Database("sqlite://")
    db.create()
    db.sync_services(load_services())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(orjson.dumps(ApiBuilder(db).services_doc()))
    console.print(f"wrote {out}")


@app.command()
def backfill(
    raw: Annotated[Path, typer.Option(help="Directory for raw responses.")] = Path("data/raw"),
    since: Annotated[str, typer.Option(help="Fetch full timelines for incidents since this date.")] = "2021-01-01",
    only: Annotated[list[str] | None, typer.Option(help="Limit to these service ids.")] = None,
    concurrency: Annotated[int, typer.Option(help="Requests in flight across all hosts.")] = 12,
    max_per_service: Annotated[int, typer.Option(help="Most recent incidents to fetch per service.")] = 1200,
) -> None:
    """Download incident histories into a resumable raw cache."""
    from mercalli.backfill import RawCache, backfill_details, backfill_history_index, snapshot_feeds
    from mercalli.catalog import load_services
    from mercalli.http import PoliteClient

    cutoff = datetime.fromisoformat(since).replace(tzinfo=UTC)
    services = [s for s in load_services() if not only or s.id in only]
    cache = RawCache(raw)

    async def run() -> None:
        async with PoliteClient(concurrency=concurrency, per_host=2, timeout=20, retries=3) as c:
            # Curated services get full timelines; pages found automatically get their history index,
            # which is enough for the blast radius model and keeps the job short.
            hist = [s for s in services if s.history and s.adapter == "statuspage"]
            done = 0

            async def one(svc) -> None:  # type: ignore[no-untyped-def]
                nonlocal done
                pages = await backfill_history_index(c, svc, cache)
                ok, failed = 0, 0
                if svc.curated:
                    ok, failed = await backfill_details(c, svc, cache, since=cutoff, max_per_service=max_per_service)
                done += 1
                console.print(
                    f"[{done}/{len(hist)}] {svc.id}: {pages} history pages, {ok} incidents fetched"
                    + (f", {failed} failed" if failed else "")
                )

            await asyncio.gather(*(one(s) for s in hist))
            saved = await snapshot_feeds(c, services, cache)
            console.print(f"saved {saved} recent feeds for pages without a full history")

    asyncio.run(run())


@app.command()
def tick(
    db: Annotated[str, typer.Option(help="Database URL.")] = DEFAULT_DB,
    publish_to: Annotated[Path | None, typer.Option("--publish", help="Write the static API here.")] = None,
    artifacts: Annotated[Path | None, typer.Option(help="Directory with model, graph and census artifacts.")] = None,
    cache: Annotated[Path | None, typer.Option(help="Directory for state kept between runs, like embeddings.")] = None,
    report: Annotated[Path | None, typer.Option(help="Write the tick report as JSON here.")] = None,
    prune: Annotated[bool, typer.Option(help="Drop data the static API no longer needs.")] = True,
) -> None:
    """Poll every status page once, update the database and optionally publish the static API."""
    from mercalli.catalog import load_services
    from mercalli.engine import Engine
    from mercalli.http import PoliteClient
    from mercalli.store import Database

    database = Database(db)
    database.create()
    services = load_services()
    database.sync_services(services)
    hooks = []
    if artifacts and (artifacts / "model").exists():
        from mercalli.ml.live import build_hooks

        hooks = build_hooks(artifacts, cache)

    async def run() -> dict[str, object]:
        async with PoliteClient(concurrency=48, per_host=2, timeout=10, retries=1) as c:
            rep = await Engine(database, services, hooks=hooks).tick(c)
        return rep.as_dict()

    rep = asyncio.run(run())
    # The public edition carries its database between runs, so it must stay small. No published
    # document reads polls, events matter for a month, and service pages show 90 days of incidents
    # (older ones live in the dataset).
    if prune:
        database.prune(
            keep_polls=timedelta(hours=6),
            keep_events=timedelta(days=30),
            keep_incidents=timedelta(days=120),
        )
    console.print(json.dumps(rep))
    if report:
        report.write_text(json.dumps(rep, indent=1))
    if publish_to:
        from mercalli.api import ApiBuilder
        from mercalli.publish import publish

        stats = publish(ApiBuilder(database, data_dir=artifacts), publish_to, tick=rep)
        console.print(f"published {stats['files']} files, {stats['bytes'] / 1e6:.1f} MB")


@app.command()
def status(
    services: Annotated[list[str] | None, typer.Argument(help="Service ids, for example github cloudflare.")] = None,
    api: Annotated[str, typer.Option(help="API base URL.")] = DEFAULT_API,
) -> None:
    """Show live status from a Mercalli API."""
    import httpx

    with httpx.Client(timeout=15, headers={"User-Agent": f"mercalli-cli/{__version__}"}) as c:
        overview = c.get(f"{api}/overview.json").raise_for_status().json()
    wanted = set(services or [])
    rows = [s for s in overview["services"] if (not wanted or s["id"] in wanted)]
    if not wanted:
        rows = [s for s in rows if s["health"] not in ("operational", "unknown")]
    table = Table(title=f"Mercalli, {overview['generated_at']}")
    for col in ("service", "health", "open incidents"):
        table.add_column(col)
    for s in rows:
        table.add_row(s["name"], s["health"].replace("_", " "), str(s["open"]))
    Console().print(table)
    if not rows:
        Console().print("Every watched service reports operational.")
    for inc in overview["active"]:
        if wanted and inc["service_id"] not in wanted:
            continue
        f = inc.get("forecast")
        eta = f" likely over in about {f['p50']:.0f} min (80% range {f['p10']:.0f} to {f['p90']:.0f})" if f else ""
        Console().print(f"[bold]{inc['service_name']}[/bold]: {inc['title']} [{inc['impact']}]{eta}")


@app.command("evaluate-live")
def evaluate_live_cmd(
    db: Annotated[str, typer.Option(help="Database URL of a running engine.")] = DEFAULT_DB,
    out: Annotated[Path | None, typer.Option(help="Write the report as JSON here.")] = None,
    model_card: Annotated[Path | None, typer.Option(help="Add the report to this model.json as `live`.")] = None,
) -> None:
    """Score the forecasts the running model actually made against how incidents really ended."""
    from mercalli.ml.live_eval import evaluate_live
    from mercalli.store import Database

    report = evaluate_live(Database(db))
    text = json.dumps(report, indent=1)
    if out:
        out.write_text(text)
    if model_card and model_card.exists():
        card = json.loads(model_card.read_text())
        card["live"] = report
        model_card.write_text(json.dumps(card, indent=1))
    typer.echo(text)


@app.command()
def mcp(
    api: Annotated[str, typer.Option(help="API base URL: the public one or your own server.")] = DEFAULT_API,
) -> None:
    """Run a Model Context Protocol server on stdio for AI agents."""
    from mercalli.mcp import serve_stdio

    serve_stdio(api)


ARTIFACTS_URL = "https://github.com/dharmeshdev1/mercalli/releases/download/artifacts/artifacts.tar.gz"


@app.command("pull-artifacts")
def pull_artifacts(
    to: Annotated[Path, typer.Option("--dir", help="Directory to put the artifacts in.")] = Path("artifacts"),
    url: Annotated[str, typer.Option(envvar="MERCALLI_ARTIFACTS_URL", help="Archive to download.")] = ARTIFACTS_URL,
    strict: Annotated[
        bool, typer.Option(help="Fail when the download fails instead of keeping what is there.")
    ] = False,
) -> None:
    """Download the published models, dependency graph and census results.

    Without them the engine still collects and serves status, just without forecasts. A failed
    download leaves any artifacts already in place untouched.
    """
    import io
    import shutil
    import tarfile
    import tempfile

    import httpx

    try:
        with httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": f"mercalli/{__version__}"}) as c:
            resp = c.get(url)
            resp.raise_for_status()
        data = resp.content
    except httpx.HTTPError as exc:
        console.print(f"could not download artifacts: {exc}")
        raise typer.Exit(1 if strict else 0) from exc
    to.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".artifacts-", dir=to.parent))
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        # The "data" filter refuses absolute paths, links out of the tree and device files.
        tar.extractall(stage, filter="data")
    if to.exists():
        shutil.rmtree(to)
    stage.rename(to)
    version = (to / "VERSION").read_text().strip() if (to / "VERSION").exists() else "unknown"
    console.print(f"artifacts {version} in {to} ({len(data) / 1e6:.1f} MB)")


def joblib_version(path: Path) -> str | None:
    import joblib

    return getattr(joblib.load(path), "version", None)


@app.command()
def train(
    raw: Annotated[Path, typer.Option(help="Raw cache written by `mercalli backfill`.")] = Path("data/raw"),
    out: Annotated[Path, typer.Option(help="Artifacts directory the engine and site read.")] = Path("artifacts"),
    census: Annotated[Path | None, typer.Option(help="Census output directory with domains.parquet.")] = None,
    calib_start: Annotated[str, typer.Option(help="Calibration period starts here.")] = "2025-07-01",
    test_start: Annotated[
        str, typer.Option(help="Test period starts here; nothing after it is used to fit.")
    ] = "2026-01-01",
    seed: Annotated[int, typer.Option(help="Random seed.")] = 0,
    skip_recovery: Annotated[
        bool, typer.Option(help="Reuse the recovery model already in the output directory.")
    ] = False,
    skip_blast: Annotated[
        bool, typer.Option(help="Reuse the blast radius model already in the output directory.")
    ] = False,
) -> None:
    """Build the dataset, train and evaluate both models, and write every artifact."""
    import shutil

    import numpy as np
    import polars as pl

    from mercalli.catalog import load_services
    from mercalli.ml import artifacts as art
    from mercalli.ml import dataset
    from mercalli.ml.embed import Embedder
    from mercalli.ml.train import Split, train_recovery
    from mercalli.ml.train_blast import train_blast

    now = datetime.now(UTC)
    services = load_services()
    split = Split(
        datetime.fromisoformat(calib_start).replace(tzinfo=UTC), datetime.fromisoformat(test_start).replace(tzinfo=UTC)
    )
    private = out / "dataset-private"
    console.print("building the dataset")
    inc, upd = dataset.build(raw, services)
    dataset.write(inc, upd, private, public=False)
    dataset.write(inc, upd, out / "data", public=True)
    info = json.loads((out / "data" / "dataset.json").read_text())
    console.print(f"  {info['incidents']} incidents, {info['updates']} updates, {info['services']} services")

    deps: dict[str, object] = {}
    if census and (census / "domains.parquet").exists():
        deps = art.census_dependencies(census / "domains.parquet", services)
        art.write_json(out / "census-deps.json", deps)
        if (census / "summary.json").exists():
            shutil.copy2(census / "summary.json", out / "census-summary.json")

    # Two passes. The evaluation pass holds out the test period and produces every reported number.
    # The production pass repeats the same procedure with the test period used for calibration and
    # nothing held out, so the deployed models learn from the most recent incidents.
    prod_split = Split(split.test_start, now + timedelta(days=1))
    embed_cache = out / "cache" / "embeddings.npz"
    deps_path = out / "census-deps.json" if deps else None
    reuse = (
        skip_recovery
        and (out / "eval" / "recovery-report.json").exists()
        and (out / "model" / "recovery.joblib").exists()
    )
    if reuse:
        rec = json.loads((out / "eval" / "recovery-report.json").read_text())
        rec["production"] = {"version": joblib_version(out / "model" / "recovery.joblib")}
    else:
        console.print("training the recovery forecaster")
        rec = train_recovery(private, out / "eval", split=split, embed_cache=embed_cache, seed=seed)
    t = rec["test"]["first_update"]
    console.print(
        f"  first update: median error {t['median_abs_error_min']:.0f} min, within 2x {t['within_2x']:.0%},"
        f" 80% interval coverage {t['coverage_80']:.0%}"
    )
    if not reuse:
        rec["production"] = train_recovery(
            private, out / "model", split=prod_split, embed_cache=embed_cache, seed=seed, evaluate=False
        )

    reuse_blast = (
        skip_blast and (out / "eval" / "blast-report.json").exists() and (out / "model" / "blast.joblib").exists()
    )
    if reuse_blast:
        bl = json.loads((out / "eval" / "blast-report.json").read_text())
        bl["production"] = {"version": joblib_version(out / "model" / "blast.joblib")}
    else:
        console.print("training the blast radius model")
        bl = train_blast(out / "data", out / "eval", split=split, deps_path=deps_path, seed=seed)
        console.print(
            f"  average precision {bl['test']['average_precision']:.3f} "
            f"(base rate {bl['test']['base_rate']:.3f}), recall@10 {bl['test']['recall_at_10']:.2f}"
        )
        bl["production"] = train_blast(
            out / "data", out / "model", split=prod_split, deps_path=deps_path, seed=seed, evaluate=False
        )

    import joblib

    blast_live = joblib.load(out / "model" / "blast.joblib").live
    graph = art.dependency_graph(services, deps, blast_live, inc, now)
    art.write_json(out / "graph.json", graph)
    intensity = art.intensity_calibration(inc, graph, now)
    art.write_json(out / "intensity.json", intensity)
    art.write_json(out / "replays.json", art.replays(inc, services))

    # Similar incident index: incidents with a full timeline from the last three years.
    recent = inc.filter((pl.col("source") == "detail") & (pl.col("start") >= now.replace(year=now.year - 3)))
    emb = Embedder(out / "cache" / "embeddings.npz")
    tv = emb.embed(recent["title"].fill_null("").to_list())
    bv = emb.embed(recent["first_body"].fill_null("").to_list())
    emb.save()
    q = tv + bv
    q /= np.linalg.norm(q, axis=1, keepdims=True).clip(1e-6)
    index = art.similar_index(recent, q, recent["id"].to_list())
    np.savez_compressed(
        out / "model" / "similar.npz",
        ids=np.array(index["ids"]),
        vectors=index["vectors"],
        meta=np.array(json.dumps(index["meta"])),
    )

    card = art.model_card(rec, bl, intensity, info)
    art.write_json(out / "model.json", card)
    console.print(f"artifacts written to {out}")


def _register_server() -> None:
    from mercalli.server.cli import register

    register(app)


_register_server()


if __name__ == "__main__":
    app()
