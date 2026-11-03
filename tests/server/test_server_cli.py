"""`register` adds `serve` to a Typer app without making the main CLI heavier."""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from mercalli.server.cli import register


@pytest.fixture
def app() -> Iterator[typer.Typer]:
    root = logging.getLogger()
    level = root.level
    cli = typer.Typer()

    @cli.callback()
    def main() -> None:
        """Mercalli."""

    register(cli)
    yield cli
    root.setLevel(level)


def test_registering_serve_imports_nothing_heavy() -> None:
    code = (
        "import sys, mercalli.server.cli; "
        "print(sorted(m for m in ('fastapi', 'uvicorn', 'sqlalchemy', 'prometheus_client') if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


def test_serve_is_registered_with_its_options(app: typer.Typer) -> None:
    # CI runners make Rich colour its help output; strip the escape codes before reading it.
    result = CliRunner().invoke(app, ["serve", "--help"], env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"})
    assert result.exit_code == 0
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    for option in (
        "--db",
        "--host",
        "--port",
        "--interval",
        "--artifacts",
        "--cache",
        "--web",
        "--config",
        "--public-url",
        "--no-scheduler",
    ):
        assert option in output


def test_a_bad_config_stops_startup_with_a_clear_error(app: typer.Typer, tmp_path: Path) -> None:
    config = tmp_path / "mercalli.yaml"
    config.write_text("channels:\n  - {type: slack, name: s, url: '${SLACK_URL}'}\n")
    result = CliRunner().invoke(
        app, ["serve", "--config", str(config), "--db", f"sqlite:///{tmp_path}/x.db"], env={"SLACK_URL": None}
    )
    assert result.exit_code == 2
    assert "environment variables that are not set: SLACK_URL" in result.output


def test_intervals_below_ten_seconds_are_refused(app: typer.Typer) -> None:
    result = CliRunner().invoke(app, ["serve", "--interval", "5"])
    assert result.exit_code == 2
