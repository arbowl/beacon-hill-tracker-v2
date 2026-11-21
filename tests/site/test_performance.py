"""At full scale (about 15k segments) dashboard queries use indexes and stay under budget."""

import json
import time
from statistics import quantiles
from typing import Any
from uuid import uuid4

from flask.testing import FlaskClient

from tests.conftest import Pusher, bundle_dict
from tracker.dashboard import queries
from tracker.dashboard.filters import DashboardFilters
from tracker.db import Connection

COMMITTEES = [f"J{n}" for n in range(1, 20)]
BUDGET_MS = 50


def _clone(source: dict[str, Any], committee_id: str) -> dict[str, Any]:
    text = (
        json.dumps(source)
        .replace(":J19:", f":{committee_id}:")
        .replace('"J19"', f'"{committee_id}"')
    )
    clone = json.loads(text)
    clone["committee"]["name"] = f"Joint Committee {committee_id}"
    clone["evaluation_run_id"] = str(uuid4())
    return clone


class Recorder:
    """Records every statement a query function runs, then delegates."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.statements: list[tuple[Any, Any]] = []

    def execute(self, query: Any, params: Any = None):
        self.statements.append((query, params))
        return self.conn.execute(query, params)


FILTERS = [
    {},
    {"committee": "J7"},
    {"status": "compliant"},
    {"status": "provisional", "committee": "J3"},
    {"sort": "bill"},
    {"sort": "bill", "committee": "J12"},
    {"sort": "due"},
    {"sort": "due", "committee": "J5"},
    {"q": "H10"},
    {"q": "nondisclosure agreements"},
    {"q": "court", "committee": "J2"},
]


def test_dashboard_at_scale(pusher: Pusher, client: FlaskClient, db: Connection):
    source = bundle_dict("J19")
    pusher.push({c: _clone(source, c) for c in COMMITTEES})
    total = db.execute("SELECT count(*) AS n FROM current_tenures").fetchone()["n"]
    assert total == 791 * len(COMMITTEES)
    db.execute("ANALYZE")

    second_page = None
    for args in FILTERS:
        f = DashboardFilters.from_args(args)
        recorder = Recorder(db)
        page = queries.segment_page(recorder, 194, f)  # type: ignore[arg-type]
        if args == {} and page.next_cursor:
            second_page = DashboardFilters.from_args({"after": page.next_cursor})
        for query, params in recorder.statements:
            text = query.as_string(db) if hasattr(query, "as_string") else query
            if "current_tenures" not in text:
                continue
            plan = json.dumps(db.execute("EXPLAIN (FORMAT JSON) " + text, params).fetchone())
            assert '"Seq Scan"' not in plan, (args, text)

    assert second_page is not None
    queries.segment_page(db, 194, second_page)

    urls = ["/?" + DashboardFilters.from_args(a).query_string() for a in FILTERS]
    urls.append("/?" + second_page.query_string(after=second_page.after))
    for url in urls:
        client.get(url)
    for headers in ({}, {"HX-Request": "true"}):
        timings = []
        for _ in range(5):
            for url in urls:
                started = time.perf_counter()
                assert client.get(url, headers=headers).status_code == 200
                timings.append((time.perf_counter() - started) * 1000)
        p95 = quantiles(timings, n=20)[-1]
        assert p95 < BUDGET_MS, (headers, round(p95, 1))
