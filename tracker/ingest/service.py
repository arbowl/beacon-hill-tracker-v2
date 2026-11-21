"""Ingest operations. Each takes a connection and runs in its own transaction."""

import hashlib
import json
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from beacon_contract import v1
from tracker.alerts import events
from tracker.db import Connection, transaction_time
from tracker.display import LEGISLATURE_TZ
from tracker.publish.rebuild import rebuild

PUBLISH_LOCK = 7_194_001
CHANGELOG_LOCK = 7_194_002


class IngestErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    SCOPE_CONFLICT = "scope_conflict"
    RUN_NOT_OPEN = "run_not_open"
    INCOMPLETE = "incomplete"
    UPLOAD_MISMATCH = "upload_mismatch"
    RELEASE_REMOVED = "release_removed"
    FUTURE_RELEASE = "future_release"


_HTTP_STATUS = {
    IngestErrorCode.NOT_FOUND: 404,
    IngestErrorCode.SCOPE_CONFLICT: 409,
    IngestErrorCode.RUN_NOT_OPEN: 409,
    IngestErrorCode.INCOMPLETE: 409,
    IngestErrorCode.UPLOAD_MISMATCH: 422,
    IngestErrorCode.RELEASE_REMOVED: 409,
    IngestErrorCode.FUTURE_RELEASE: 422,
}


class IngestError(Exception):
    def __init__(self, code: IngestErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS[self.code]


def run_state(conn: Connection, push_id: UUID) -> v1.RunState | None:
    row = conn.execute(
        "SELECT r.run_id, r.status, r.scope, r.opened_at, r.published_at,"
        " coalesce(array_agg(rc.committee_id ORDER BY rc.committee_id)"
        "          FILTER (WHERE rc.committee_id IS NOT NULL), '{}') AS uploaded"
        " FROM runs r LEFT JOIN run_committees rc USING (run_id)"
        " WHERE r.run_id = %s GROUP BY r.run_id",
        (push_id,),
    ).fetchone()
    if row is None:
        return None
    return v1.RunState(
        push_id=row["run_id"],
        status=row["status"],
        committees=tuple(row["scope"]),
        uploaded=tuple(row["uploaded"]),
        opened_at=row["opened_at"],
        published_at=row["published_at"],
    )


def _require_state(conn: Connection, push_id: UUID) -> v1.RunState:
    state = run_state(conn, push_id)
    if state is None:
        raise IngestError(IngestErrorCode.NOT_FOUND, f"No push {push_id}")
    return state


def _lock_run(conn: Connection, push_id: UUID) -> dict:
    row = conn.execute(
        "SELECT run_id, session, code_version, created_at, scope, status FROM runs"
        " WHERE run_id = %s FOR UPDATE",
        (push_id,),
    ).fetchone()
    if row is None:
        raise IngestError(IngestErrorCode.NOT_FOUND, f"No push {push_id}")
    return row


def open_run(conn: Connection, manifest: v1.RunManifest, api_key_id: int) -> v1.RunState:
    """Open a push, or return the existing one if it has the same session and scope."""
    with conn.transaction():
        inserted = conn.execute(
            "INSERT INTO runs (run_id, session, code_version, created_at, scope, api_key_id)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING RETURNING run_id",
            (
                manifest.push_id,
                manifest.session,
                manifest.code_version,
                manifest.created_at,
                list(manifest.committees),
                api_key_id,
            ),
        ).fetchone()
        if inserted is None:
            row = _lock_run(conn, manifest.push_id)
            if (row["session"], tuple(row["scope"])) != (manifest.session, manifest.committees):
                raise IngestError(
                    IngestErrorCode.SCOPE_CONFLICT,
                    f"Push {manifest.push_id} was opened with a different session or committees",
                )
        return _require_state(conn, manifest.push_id)


def _json(models: tuple[BaseModel, ...]) -> str:
    return json.dumps([m.model_dump(mode="json") for m in models])


def upload(
    conn: Connection, push_id: UUID, committee_id: str, bundle: v1.CommitteeBundle, sha256: str
) -> v1.RunState:
    """Store one committee's bundle for an open push, replacing any earlier upload of it."""
    with conn.transaction():
        row = _lock_run(conn, push_id)
        if row["status"] != v1.RunStatus.OPEN:
            raise IngestError(IngestErrorCode.RUN_NOT_OPEN, f"Push {push_id} is {row['status']}")
        manifest = v1.RunManifest(
            schema_version=v1.SCHEMA_VERSION,
            push_id=push_id,
            session=row["session"],
            code_version=row["code_version"],
            created_at=row["created_at"],
            committees=tuple(row["scope"]),
        )
        try:
            v1.check_upload(manifest, committee_id, bundle)
        except v1.UploadMismatch as exc:
            raise IngestError(IngestErrorCode.UPLOAD_MISMATCH, str(exc)) from exc
        _store_bundle(conn, push_id, bundle, sha256)
        return _require_state(conn, push_id)


def _store_bundle(conn: Connection, run_id: UUID, b: v1.CommitteeBundle, sha256: str) -> None:
    c = b.committee
    conn.execute(
        "DELETE FROM run_committees WHERE run_id = %s AND committee_id = %s",
        (run_id, c.committee_id),
    )
    conn.execute(
        "INSERT INTO run_committees (run_id, committee_id, evaluation_run_id, ruleset_id,"
        " ruleset_version, as_of, name, chamber, url, bundle_sha256, tenure_count, uploaded_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())",
        (
            run_id,
            c.committee_id,
            b.evaluation_run_id,
            b.ruleset_id,
            b.ruleset_version,
            b.as_of,
            c.name,
            c.chamber,
            str(c.url),
            sha256,
            len(b.tenures),
        ),
    )
    with conn.cursor() as cur:
        with cur.copy(
            "COPY run_bills (run_id, committee_id, bill_id, title, url) FROM STDIN"
        ) as cp:
            for bill in b.bills:
                cp.write_row((run_id, c.committee_id, bill.bill_id, bill.title, str(bill.url)))
        with cur.copy(
            "COPY committee_outputs (run_id, committee_id, bill_id, title, originated_on,"
            " source_bills) FROM STDIN"
        ) as cp:
            for o in b.committee_output:
                cp.write_row(
                    (
                        run_id,
                        c.committee_id,
                        o.bill_id,
                        o.title,
                        o.originated_on,
                        list(o.source_bills),
                    )
                )
        with cur.copy(
            "COPY tenures (run_id, tenure_id, session, bill_id, committee_id, referred_on, label,"
            " input_fingerprint) FROM STDIN"
        ) as cp:
            for t in b.tenures:
                cp.write_row(
                    (
                        run_id,
                        t.tenure_id,
                        b.session,
                        t.bill_id,
                        t.committee_id,
                        t.referred_on,
                        t.label,
                        t.input_fingerprint,
                    )
                )
        with cur.copy(
            "COPY requirements (run_id, tenure_id, requirement, obligation_id, status, reason,"
            " detail, due, resolves_on, completed_on) FROM STDIN"
        ) as cp:
            for t in b.tenures:
                for r in t.requirements:
                    cp.write_row(
                        (
                            run_id,
                            t.tenure_id,
                            r.requirement_id,
                            r.obligation_id,
                            r.status,
                            r.reason,
                            r.detail,
                            r.due,
                            r.resolves_on,
                            r.completed_on,
                        )
                    )
        with cur.copy(
            "COPY requirement_audit (run_id, tenure_id, requirement, derivation, evidence,"
            " clauses) FROM STDIN"
        ) as cp:
            for t in b.tenures:
                for r in t.requirements:
                    cp.write_row(
                        (
                            run_id,
                            t.tenure_id,
                            r.requirement_id,
                            _json(r.derivation),
                            _json(r.evidence),
                            _json(r.clauses),
                        )
                    )
    conn.cursor().executemany(
        "INSERT INTO clauses (text_hash, source, rule, paragraph, text, first_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (text_hash) DO NOTHING",
        [
            (cl.text_hash, cl.ref.source, cl.ref.rule, cl.ref.paragraph, cl.text, run_id)
            for cl in b.clauses
        ],
    )


def publish(conn: Connection, push_id: UUID) -> v1.RunState:
    """Make every committee in the push live at once. Publishing twice returns the first result."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (PUBLISH_LOCK,))
        row = _lock_run(conn, push_id)
        if row["status"] == v1.RunStatus.PUBLISHED:
            return _require_state(conn, push_id)
        if row["status"] != v1.RunStatus.OPEN:
            raise IngestError(IngestErrorCode.RUN_NOT_OPEN, f"Push {push_id} is {row['status']}")
        state = _require_state(conn, push_id)
        missing = sorted(set(state.committees) - set(state.uploaded))
        if missing:
            raise IngestError(IngestErrorCode.INCOMPLETE, f"Not uploaded: {', '.join(missing)}")
        now = transaction_time(conn)
        session, committees = row["session"], list(row["scope"])
        params = {"run": push_id, "session": session, "committees": committees, "now": now}
        conn.execute(
            "INSERT INTO committee_current (session, committee_id, run_id, as_of)"
            " SELECT %(session)s, committee_id, run_id, as_of FROM run_committees"
            " WHERE run_id = %(run)s"
            " ON CONFLICT (session, committee_id)"
            " DO UPDATE SET run_id = EXCLUDED.run_id, as_of = EXCLUDED.as_of",
            params,
        )
        conn.execute(
            "INSERT INTO committee_publications (session, committee_id, run_id, published_at)"
            " SELECT %(session)s, committee_id, %(run)s, %(now)s FROM run_committees"
            " WHERE run_id = %(run)s",
            params,
        )
        upsert_committees(conn, session, push_id, committees)
        conn.execute(
            "UPDATE runs SET status = 'published', published_at = %(now)s WHERE run_id = %(run)s",
            params,
        )
        rebuild(conn, session, committees, push_id, now)
        events.record_publish(conn, session, committees, now)
        return _require_state(conn, push_id)


def upsert_committees(conn: Connection, session: int, run_id: UUID, committees: list[str]) -> None:
    """Point the committee dimension at the names and URLs this run asserted."""
    conn.execute(
        "INSERT INTO committees (session, committee_id, name, chamber, url, last_run_id)"
        " SELECT %s, committee_id, name, chamber, url, run_id FROM run_committees"
        " WHERE run_id = %s AND committee_id = ANY(%s)"
        " ON CONFLICT (session, committee_id) DO UPDATE SET name = EXCLUDED.name,"
        " chamber = EXCLUDED.chamber, url = EXCLUDED.url, last_run_id = EXCLUDED.last_run_id",
        (session, run_id, committees),
    )


def abandon(conn: Connection, push_id: UUID) -> v1.RunState:
    """Discard an unpublished push and its uploads."""
    with conn.transaction():
        row = _lock_run(conn, push_id)
        if row["status"] not in (v1.RunStatus.OPEN, v1.RunStatus.ABANDONED):
            raise IngestError(IngestErrorCode.RUN_NOT_OPEN, f"Push {push_id} is {row['status']}")
        conn.execute("DELETE FROM run_committees WHERE run_id = %s", (push_id,))
        conn.execute("UPDATE runs SET status = 'abandoned' WHERE run_id = %s", (push_id,))
        return _require_state(conn, push_id)


def record_changelog(
    conn: Connection, changelog: v1.Changelog, api_key_id: int
) -> v1.ChangelogState:
    """Store each release whose text differs from the one shown. Releases are never removed."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (CHANGELOG_LOCK,))
        now = transaction_time(conn)
        future = [
            r.version
            for r in changelog.releases
            if r.released_on > now.astimezone(LEGISLATURE_TZ).date()
        ]
        if future:
            raise IngestError(IngestErrorCode.FUTURE_RELEASE, f"Dated in the future: {future}")
        shown = {
            r["version"]: r["content_hash"]
            for r in conn.execute(
                "SELECT DISTINCT ON (version) version, content_hash FROM changelog_releases"
                " ORDER BY version, current_since DESC"
            )
        }
        removed = sorted(set(shown) - {r.version for r in changelog.releases})
        if removed:
            raise IngestError(
                IngestErrorCode.RELEASE_REMOVED, f"Published releases are missing: {removed}"
            )
        updated = []
        for r in changelog.releases:
            content_hash = hashlib.sha256(r.model_dump_json().encode("utf-8")).hexdigest()
            if shown.get(r.version) == content_hash:
                continue
            major, minor, patch = r.semver
            conn.execute(
                "INSERT INTO changelog_releases (version, content_hash, major, minor, patch,"
                " released_on, sections, code_version, api_key_id, received_at, current_since)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (version, content_hash)"
                " DO UPDATE SET current_since = EXCLUDED.current_since",
                (
                    r.version,
                    content_hash,
                    major,
                    minor,
                    patch,
                    r.released_on,
                    _json(r.sections),
                    changelog.code_version,
                    api_key_id,
                    now,
                    now,
                ),
            )
            updated.append(r.version)
        return v1.ChangelogState(
            releases=tuple(r.version for r in changelog.releases), updated=tuple(updated)
        )
