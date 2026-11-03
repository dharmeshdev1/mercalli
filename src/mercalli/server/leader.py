"""Which replica runs the scheduler.

Every replica serves the API, but only one may tick and send notifications, or each alert would
go out once per replica. On PostgreSQL that one is whoever holds a session level advisory lock,
taken on a connection kept open for as long as the process leads. If that connection breaks, the
database releases the lock and another replica takes over within one follower poll. During a
rolling update the new pod simply waits until the old one exits.

SQLite needs no election: a SQLite file belongs to one process.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from mercalli.store import Database

log = logging.getLogger("mercalli.server")

LOCK_KEY = int.from_bytes(hashlib.sha256(b"mercalli.scheduler").digest()[:8], "big", signed=True)


class Leadership:
    def __init__(self, db: Database, *, enabled: bool) -> None:
        self.enabled = enabled
        self._engine: Engine | None = None
        if enabled and db.engine.dialect.name == "postgresql":
            # Its own unpooled engine in autocommit mode: the lock outlives any transaction and the
            # connection holding it never sits idle inside one.
            self._engine = create_engine(db.engine.url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
        self._conn: Connection | None = None

    def hold(self) -> bool:
        """Take the scheduler lock, or confirm this process still has it. Blocking."""
        if not self.enabled:
            return False
        if self._engine is None:
            return True
        if self._conn is not None:
            try:
                self._conn.execute(text("SELECT 1"))
                return True
            except DBAPIError:
                log.warning("lost the scheduler lock: its database connection broke")
                self._drop()
        conn = self._engine.connect()
        try:
            got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}).scalar())
        except BaseException:
            conn.close()
            raise
        if got:
            self._conn = conn
            return True
        conn.close()
        return False

    def release(self) -> None:
        if self._conn is not None:
            # Closing the connection releases the lock too, so a failed unlock changes nothing.
            with contextlib.suppress(DBAPIError):
                self._conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
            self._drop()
        if self._engine is not None:
            self._engine.dispose()

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except DBAPIError:
                log.debug("closing the lock connection failed", exc_info=True)
