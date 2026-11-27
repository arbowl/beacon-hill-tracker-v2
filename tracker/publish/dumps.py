"""Data dumps: the live data of a session as current.csv.gz and current.json.gz.

The JSON dump rebuilds each live committee's CommitteeBundle from the detail tables and
validates it against the contract, so a file that is served is one the client could have sent.
"""

import gzip
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg.pq import TransactionStatus

from beacon_contract import v1
from tracker.db import Connection
from tracker.enums import DumpFormat
from tracker.publish import records

DUMP_LOCK = 7_194_004
REQUIREMENTS = ("hearing_notice", "summary_posted", "votes_posted", "report_out_deadline")

_CSV_COLUMNS = ",\n".join(
    f"max(r.{field}::text) FILTER (WHERE r.requirement = '{req}') AS {req}_{field}"
    for req in REQUIREMENTS
    for field in ("status", "reason", "due", "completed_on")
)
_CSV = f"""
COPY (
  SELECT ct.session, ct.tenure_id, ct.bill_id, ct.title, ct.committee_id, ct.committee_name,
         ct.referred_on, ct.referral_observed, ct.label,
         {_CSV_COLUMNS},
         rc.as_of AS data_as_of, rc.evaluation_run_id, rc.ruleset_id, rc.ruleset_version
  FROM current_tenures ct
  JOIN run_committees rc ON rc.run_id = ct.run_id AND rc.committee_id = ct.committee_id
  LEFT JOIN requirements r ON r.run_id = ct.run_id AND r.tenure_id = ct.tenure_id
  WHERE ct.session = %s
  GROUP BY ct.session, ct.tenure_id, ct.bill_id, ct.bill_num, ct.title, ct.committee_id,
           ct.committee_name, ct.referred_on, ct.referral_observed, ct.label, rc.as_of,
           rc.evaluation_run_id, rc.ruleset_id, rc.ruleset_version
  ORDER BY ct.committee_id, ct.bill_num, ct.bill_id, ct.tenure_id
) TO STDOUT WITH (FORMAT csv, HEADER)
"""


@dataclass(frozen=True)
class LiveData:
    """What a session's dumps are built from."""

    session: int
    version: str
    as_of: datetime
    runs: tuple[tuple[str, UUID], ...]


@dataclass(frozen=True)
class Dump:
    id: int
    session: int
    format: DumpFormat
    as_of: datetime
    built_at: datetime
    sha256: str
    size: int


def live_data(conn: Connection, session: int) -> LiveData | None:
    rows = conn.execute(
        "SELECT committee_id, run_id, as_of FROM committee_current WHERE session = %s"
        " ORDER BY committee_id",
        (session,),
    ).fetchall()
    if not rows:
        return None
    runs = tuple((r["committee_id"], r["run_id"]) for r in rows)
    version = hashlib.sha256("\n".join(f"{c}:{run}" for c, run in runs).encode()).hexdigest()[:16]
    return LiveData(session, version, max(r["as_of"] for r in rows), runs)


def _gzip_writer(buffer: io.BytesIO) -> gzip.GzipFile:
    """Deterministic gzip: no timestamp or name, so equal data gives equal bytes."""
    return gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0)


def build_csv(conn: Connection, session: int) -> bytes:
    """One row per live bill segment."""
    buffer = io.BytesIO()
    with _gzip_writer(buffer) as out, conn.cursor() as cur, cur.copy(_CSV, (session,)) as copy:
        for chunk in copy:
            out.write(chunk)
    return buffer.getvalue()


def bundle(conn: Connection, run_id: UUID, committee_id: str) -> v1.CommitteeBundle:
    """Rebuild a committee's bundle as uploaded in run_id; raises if its detail was pruned."""
    head = conn.execute(
        "SELECT rc.*, ru.session FROM run_committees rc JOIN runs ru ON ru.run_id = rc.run_id"
        " WHERE rc.run_id = %s AND rc.committee_id = %s AND rc.pruned_at IS NULL",
        (run_id, committee_id),
    ).fetchone()
    if head is None:
        raise LookupError(f"{committee_id} in {run_id} has no retained detail")
    key = (run_id, committee_id)
    bills = conn.execute(
        "SELECT bill_id, title, url FROM run_bills WHERE run_id = %s AND committee_id = %s"
        " ORDER BY substring(bill_id FROM 2)::int, bill_id",
        key,
    ).fetchall()
    outputs = conn.execute(
        "SELECT bill_id, title, originated_on, source_bills FROM committee_outputs"
        " WHERE run_id = %s AND committee_id = %s"
        " ORDER BY substring(bill_id FROM 2)::int, bill_id",
        key,
    ).fetchall()
    owed_nothing = conn.execute(
        "SELECT tenure_id, bill_id, committee_id, referred_on, reason, awaiting, carried_by,"
        " note"
        " FROM no_requirement_tenures WHERE run_id = %s AND committee_id = %s"
        " ORDER BY substring(bill_id FROM 2)::int, bill_id, tenure_id",
        key,
    ).fetchall()
    return v1.CommitteeBundle.model_validate(
        {
            "schema_version": v1.SCHEMA_VERSION,
            "session": head["session"],
            "evaluation_run_id": head["evaluation_run_id"],
            "ruleset_id": head["ruleset_id"],
            "ruleset_version": head["ruleset_version"],
            "as_of": head["as_of"],
            "committee": {
                "committee_id": committee_id,
                "name": head["name"],
                "chamber": head["chamber"],
                "url": head["url"],
            },
            "bills": bills,
            "tenures": records.tenure_dicts(conn, run_id, committee_id),
            "clauses": records.cited_clause_dicts(conn, run_id, committee_id),
            "committee_output": outputs,
            "no_requirements": owed_nothing,
        }
    )


def build_json(conn: Connection, live: LiveData) -> bytes:
    """{"schema_version", "session", "data_version", "as_of", "bundles": [CommitteeBundle]}."""
    buffer = io.BytesIO()
    with _gzip_writer(buffer) as out:
        head = {
            "schema_version": v1.SCHEMA_VERSION,
            "session": live.session,
            "data_version": live.version,
            "as_of": live.as_of.isoformat(),
        }
        out.write(json.dumps(head)[:-1].encode() + b', "bundles": [')
        for n, (committee_id, run_id) in enumerate(live.runs):
            if n:
                out.write(b",")
            out.write(bundle(conn, run_id, committee_id).model_dump_json().encode())
        out.write(b"]}")
    return buffer.getvalue()


def refresh(conn: Connection, session: int, keep_recent: int, keep_monthly: bool) -> list[Dump]:
    """Make the session's live data downloadable, then apply retention.

    Does nothing for a format whose newest file already holds the live data, so it is cheap to
    run on a schedule. A file for data that is live again, as after a rollback, becomes current
    without being rebuilt. Call with no transaction open, so the build reads one
    consistent snapshot.
    """
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("dumps.refresh needs a connection with no transaction open")
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (DUMP_LOCK,))
        live = live_data(conn, session)
        if live is None:
            return []
        for fmt in DumpFormat:
            newest = conn.execute(
                "SELECT data_version FROM data_dumps WHERE session = %s AND format = %s"
                " ORDER BY current_since DESC LIMIT 1",
                (session, fmt),
            ).fetchone()
            if newest is not None and newest["data_version"] == live.version:
                continue
            reused = conn.execute(
                "UPDATE data_dumps SET current_since = now()"
                " WHERE session = %s AND format = %s AND data_version = %s RETURNING id",
                (session, fmt, live.version),
            ).fetchone()
            if reused is not None:
                continue
            data = build_csv(conn, session) if fmt == DumpFormat.CSV_GZ else build_json(conn, live)
            conn.execute(
                "INSERT INTO data_dumps (session, format, data_version, as_of, built_at,"
                " current_since, sha256, size, bytes) VALUES (%s, %s, %s, %s, now(), now(), %s,"
                " %s, %s)",
                (
                    session,
                    fmt,
                    live.version,
                    live.as_of,
                    hashlib.sha256(data).hexdigest(),
                    len(data),
                    data,
                ),
            )
        _retain(conn, keep_recent, keep_monthly)
        return current(conn, session)


def _retain(conn: Connection, keep_recent: int, keep_monthly: bool) -> None:
    conn.execute(
        "DELETE FROM data_dumps WHERE id NOT IN ("
        "  SELECT id FROM (SELECT id, row_number() OVER ("
        "    PARTITION BY session, format ORDER BY current_since DESC) AS n FROM data_dumps) r"
        "  WHERE n <= %(recent)s)"
        " AND NOT (%(monthly)s AND id IN ("
        "  SELECT DISTINCT ON (session, format,"
        "    date_trunc('month', built_at AT TIME ZONE 'America/New_York')) id FROM data_dumps"
        "  ORDER BY session, format,"
        "    date_trunc('month', built_at AT TIME ZONE 'America/New_York'), built_at))",
        {"recent": keep_recent, "monthly": keep_monthly},
    )


def current(conn: Connection, session: int) -> list[Dump]:
    """The newest file of each format for the session, without its bytes."""
    rows = conn.execute(
        "SELECT DISTINCT ON (format) id, session, format, as_of, built_at, sha256, size"
        " FROM data_dumps WHERE session = %s ORDER BY format, current_since DESC",
        (session,),
    ).fetchall()
    return [
        Dump(
            r["id"],
            r["session"],
            DumpFormat(r["format"]),
            r["as_of"],
            r["built_at"],
            r["sha256"],
            r["size"],
        )
        for r in rows
    ]


def contents(conn: Connection, dump_id: int) -> bytes:
    row = conn.execute("SELECT bytes FROM data_dumps WHERE id = %s", (dump_id,)).fetchone()
    if row is None:
        raise LookupError(f"no dump {dump_id}")
    return bytes(row["bytes"])
