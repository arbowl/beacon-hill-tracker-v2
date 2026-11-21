"""Connection pool and schema migrations."""

import atexit
import hashlib
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from typing import Any, cast

import psycopg
from flask import Flask, current_app
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from tracker.config import Settings

Connection = psycopg.Connection[DictRow]
_EXTENSION = "tracker.pool"


def connect(url: str, **kwargs: Any) -> Connection:
    """A standalone connection with dict rows, for CLI commands and tests."""
    return psycopg.connect(url, row_factory=dict_row, **kwargs)


def transaction_time(conn: Connection) -> datetime:
    """now() of the current transaction, so every row written in it shares one instant."""
    row = conn.execute("SELECT now() AS now").fetchone()
    assert row is not None
    return row["now"]


def init_pool(app: Flask, settings: Settings) -> None:
    connections = ConnectionPool(
        settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        kwargs={"row_factory": dict_row},
        open=True,
    )
    atexit.register(connections.close)
    app.extensions[_EXTENSION] = connections


def pool() -> ConnectionPool[Connection]:
    return cast(ConnectionPool[Connection], current_app.extensions[_EXTENSION])


@dataclass(frozen=True)
class Migration:
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def migrations() -> list[Migration]:
    """Every migration file, in filename order."""
    folder = files("tracker") / "migrations"
    found = sorted((p for p in folder.iterdir() if p.name.endswith(".sql")), key=lambda p: p.name)
    return [Migration(p.name, p.read_text(encoding="utf-8")) for p in found]


class MigrationError(RuntimeError):
    """An applied migration no longer matches its file."""


def migrate(conn: Connection) -> list[str]:
    """Apply pending migrations, each in its own transaction; returns their names."""
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY,"
            " checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
        )
    applied = {
        r["name"]: r["checksum"]
        for r in conn.execute("SELECT name, checksum FROM schema_migrations")
    }
    done = []
    for m in migrations():
        if m.name in applied:
            if applied[m.name] != m.checksum:
                raise MigrationError(f"{m.name} changed after it was applied")
            continue
        with conn.transaction():
            conn.execute(m.sql)
            conn.execute(
                "INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)",
                (m.name, m.checksum),
            )
        done.append(m.name)
    return done
