"""Site tests run against a throwaway database on a real Postgres (compose.yaml)."""

import gzip
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from flask import Flask
from flask.testing import FlaskClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from beacon_contract import v1
from tracker import create_app
from tracker.auth.keys import create_key
from tracker.config import Settings
from tracker.db import Connection, connect, migrate
from tracker.enums import KeyScope, UserRole
from tracker.updates import posts

ADMIN_URL = os.environ.get(
    "TRACKER_TEST_ADMIN_URL", "postgresql://tracker:tracker@127.0.0.1:54329/tracker"
)
BUNDLES = Path(__file__).resolve().parent / "fixtures" / "bundles"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    name = f"tracker_test_{uuid4().hex[:10]}"
    with connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_conninfo(ADMIN_URL, dbname=name)
    try:
        with connect(url, autocommit=True) as conn:
            migrate(conn)
        yield url
    finally:
        with connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        secret_key="t" * 32,
        db_pool_max=4,
        secure_cookies=False,
    )


@pytest.fixture
def db(settings: Settings) -> Iterator[Connection]:
    with connect(settings.database_url, autocommit=True) as conn:
        tables = [
            r["tablename"]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                " AND tablename <> 'schema_migrations'"
            )
        ]
        conn.execute(
            sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                sql.SQL(", ").join(map(sql.Identifier, tables))
            )
        )
        yield conn


@pytest.fixture
def app(settings: Settings, db: Connection) -> Iterator[Flask]:
    app = create_app(settings)
    app.config["TESTING"] = True
    posts.install(app, ())
    yield app
    app.extensions["tracker.pool"].close()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def make_user(conn: Connection, email: str, role: UserRole) -> int:
    row = conn.execute(
        "INSERT INTO users (email, role) VALUES (%s, %s) RETURNING id", (email, role)
    ).fetchone()
    assert row is not None
    return row["id"]


@pytest.fixture
def ingest_key(db: Connection) -> str:
    user_id = make_user(db, "webmaster@example.org", UserRole.WEBMASTER)
    return create_key(db, user_id, KeyScope.INGEST, "test").secret


def bundle_dict(committee_id: str) -> dict[str, Any]:
    return json.loads(gzip.decompress((BUNDLES / f"{committee_id}.bundle.json.gz").read_bytes()))


def gz(bundle: dict[str, Any]) -> bytes:
    """Validate a bundle dict and return it gzipped, as the client sends it."""
    return gzip.compress(v1.CommitteeBundle.model_validate(bundle).model_dump_json().encode())


def manifest_json(push_id: UUID, committees: list[str], session: int = 194) -> str:
    return v1.RunManifest(
        schema_version=1,
        push_id=push_id,
        session=session,
        code_version="test",
        created_at=datetime.now(UTC),
        committees=tuple(committees),
    ).model_dump_json()


class Pusher:
    """Drives the ingest API the way the client does."""

    def __init__(self, client: FlaskClient, key: str) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bearer {key}"}

    def open(self, push_id: UUID, committees: list[str], **kw: Any):
        return self.client.post(
            "/api/ingest/v1/runs",
            data=manifest_json(push_id, committees, **kw),
            headers=self.headers,
            content_type="application/json",
        )

    def upload(self, push_id: UUID, committee_id: str, body: bytes):
        return self.client.put(
            f"/api/ingest/v1/runs/{push_id}/committees/{committee_id}",
            data=body,
            headers=self.headers | {"Content-Encoding": "gzip"},
            content_type="application/json",
        )

    def changelog(self, body: dict[str, Any]):
        return self.client.put(
            "/api/ingest/v1/changelog",
            data=json.dumps(body, default=str),
            headers=self.headers,
            content_type="application/json",
        )

    def publish(self, push_id: UUID):
        return self.client.post(f"/api/ingest/v1/runs/{push_id}/publish", headers=self.headers)

    def push(self, bundles: dict[str, dict[str, Any]], push_id: UUID | None = None) -> UUID:
        push_id = push_id or uuid4()
        assert self.open(push_id, list(bundles)).status_code == 200
        for committee_id, b in bundles.items():
            response = self.upload(push_id, committee_id, gz(b))
            assert response.status_code == 200, response.get_json()
        response = self.publish(push_id)
        assert response.status_code == 200, response.get_json()
        return push_id


@pytest.fixture
def pusher(client: FlaskClient, ingest_key: str) -> Pusher:
    return Pusher(client, ingest_key)
