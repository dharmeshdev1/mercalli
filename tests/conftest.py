from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mercalli.catalog import Service
from mercalli.domain import Category
from mercalli.store import Base, Database

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def github() -> Service:
    return Service(
        id="github",
        name="GitHub",
        category=Category.DEVTOOLS,
        adapter="statuspage",
        base_url="https://www.githubstatus.com",
        domains=["github.com"],
        page_id="kctbh9vrtdwd",
        flavor="atlassian",
        history=True,
    )


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    """SQLite by default; set MERCALLI_TEST_DB to run the same tests against PostgreSQL."""
    url = os.environ.get("MERCALLI_TEST_DB")
    if not url:
        d = Database(f"sqlite:///{tmp_path / 'test.db'}")
        d.create()
        yield d
        return
    d = Database(url)
    Base.metadata.drop_all(d.engine)
    d.create()
    yield d
    d.engine.dispose()
    Base.metadata.drop_all(Database(url).engine)


def t(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)
