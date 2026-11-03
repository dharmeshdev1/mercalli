"""The `mercalli serve` command. `register` adds it to the main Typer app."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer

LOG_LEVELS = ("debug", "info", "warning", "error")


def register(app: typer.Typer) -> None:
    app.command("serve")(serve)


def serve(
    db: Annotated[
        str, typer.Option(envvar=["MERCALLI_DB", "DATABASE_URL"], help="sqlite:///... or postgresql://... URL.")
    ] = "sqlite:///mercalli.db",
    host: Annotated[str, typer.Option(envvar="MERCALLI_HOST", help="Address to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(envvar="MERCALLI_PORT", help="Port to listen on.")] = 8080,
    interval: Annotated[
        float, typer.Option(min=10.0, envvar="MERCALLI_INTERVAL", help="Seconds between polls of every status page.")
    ] = 60.0,
    jitter: Annotated[
        float, typer.Option(min=0.0, max=0.5, help="Random spread of each tick start, as a fraction of the interval.")
    ] = 0.1,
    artifacts: Annotated[
        Path | None, typer.Option(envvar="MERCALLI_ARTIFACTS", help="Directory with model, graph and census artifacts.")
    ] = None,
    cache: Annotated[
        Path | None, typer.Option(envvar="MERCALLI_CACHE", help="Directory for state kept between runs.")
    ] = None,
    web: Annotated[Path | None, typer.Option(envvar="MERCALLI_WEB", help="The exported web app to serve at /.")] = None,
    config: Annotated[
        Path | None, typer.Option(envvar="MERCALLI_CONFIG", help="YAML config: your stack and notification channels.")
    ] = None,
    public_url: Annotated[
        str | None, typer.Option(envvar="MERCALLI_PUBLIC_URL", help="This server's URL, for links in alerts.")
    ] = None,
    scheduler: Annotated[
        bool, typer.Option("--scheduler/--no-scheduler", help="Poll and notify, or only serve the API.")
    ] = True,
    access_log: Annotated[bool, typer.Option("--access-log/--no-access-log", help="Log every request.")] = False,
    log_level: Annotated[
        str, typer.Option(envvar="MERCALLI_LOG_LEVEL", help="debug, info, warning or error.")
    ] = "info",
) -> None:
    """Run the self-hosted server: live API, live stream, alerts and the web app."""
    from mercalli.notify.config import Config, ConfigError, load_config
    from mercalli.server.app import create_app
    from mercalli.server.runner import run_server
    from mercalli.server.settings import ServerSettings

    if log_level.lower() not in LOG_LEVELS:
        raise typer.BadParameter(f"choose one of {', '.join(LOG_LEVELS)}", param_hint="--log-level")
    _setup_logging(log_level)
    try:
        cfg = load_config(config) if config else Config()
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    settings = ServerSettings(
        db_url=db,
        interval=interval,
        jitter=jitter,
        artifacts=artifacts,
        cache_dir=cache,
        web=web,
        config_path=config,
        public_url=public_url or None,
        scheduler=scheduler,
    )
    app = create_app(settings, config=cfg)
    run_server(app, host=host, port=port, access_log=access_log, grace=settings.shutdown_grace)


def _setup_logging(level: str) -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    root.setLevel(level.upper())
    # httpx logs every request at INFO, which would be hundreds of lines a minute.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
