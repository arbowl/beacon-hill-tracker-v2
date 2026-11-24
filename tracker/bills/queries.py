"""Bill permalink and audit-trail queries."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from beacon_contract.v1 import (
    ProvisionalReason,
    RequirementId,
    RequirementStatus,
    StepRole,
)
from tracker.db import Connection
from tracker.display import (
    CHIP_CLASS,
    CHIP_TEXT,
    DUE_ROLES,
    REASON_TEXT,
    REQUIREMENT_TEXT,
    ROLE_TEXT,
    clause_long,
    clause_short,
)

_REQUIREMENT_ORDER = {r: i for i, r in enumerate(RequirementId)}


@dataclass(frozen=True)
class Cite:
    short: str
    long: str
    text: str
    superseded: bool


@dataclass(frozen=True)
class Step:
    value: date | None
    description: str
    role: str | None
    is_due: bool
    cite: Cite | None


@dataclass(frozen=True)
class EvidenceView:
    source: str
    occurred_on: date | None
    text: str
    url: str | None
    observation_id: str
    row_index: int | None


@dataclass(frozen=True)
class RequirementView:
    name: str
    chip_text: str
    chip_css: str
    reason: str | None
    detail: str
    due: date | None
    resolves_on: date | None
    completed_on: date | None
    steps: list[Step]
    evidence: list[EvidenceView]
    superseded_rule: bool


@dataclass(frozen=True)
class HistoryEntry:
    changed_at: datetime
    from_label: str | None
    to_label: str | None
    requirements: list[str]


@dataclass(frozen=True)
class Provenance:
    tenure_id: str
    push_id: str
    evaluation_run_id: str
    ruleset: str
    as_of: datetime
    input_fingerprint: str
    observations: list[str]


@dataclass(frozen=True)
class Audit:
    requirements: list[RequirementView]
    history: list[HistoryEntry]
    provenance: Provenance


def bill_segments(conn: Connection, session: int, bill_id: str) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT * FROM current_tenures WHERE session = %s AND bill_id = %s"
        " ORDER BY referred_on NULLS LAST, committee_id",
        (session, bill_id),
    ).fetchall()


def bill_owed_nothing(conn: Connection, session: int, bill_id: str) -> list[dict[str, Any]]:
    """Live referrals of the bill that owed nothing; they have no label."""
    return conn.execute(
        "SELECT n.tenure_id, n.committee_id, rc.name AS committee_name, n.referred_on,"
        " n.reason, n.awaiting, n.carried_by, b.title, b.url AS bill_url"
        " FROM committee_current cc"
        " JOIN run_committees rc ON rc.run_id = cc.run_id AND rc.committee_id = cc.committee_id"
        " JOIN no_requirement_tenures n"
        "   ON n.run_id = cc.run_id AND n.committee_id = cc.committee_id"
        " JOIN run_bills b ON b.run_id = n.run_id AND b.committee_id = n.committee_id"
        "                 AND b.bill_id = n.bill_id"
        " WHERE cc.session = %s AND n.bill_id = %s ORDER BY n.referred_on, n.committee_id",
        (session, bill_id),
    ).fetchall()


def committee_owed_nothing(conn: Connection, session: int, committee_id: str) -> dict[str, int]:
    """How many live referrals to the committee owed nothing, by reason."""
    rows = conn.execute(
        "SELECT n.reason, count(*) AS n FROM committee_current cc"
        " JOIN no_requirement_tenures n"
        "   ON n.run_id = cc.run_id AND n.committee_id = cc.committee_id"
        " WHERE cc.session = %s AND cc.committee_id = %s GROUP BY n.reason",
        (session, committee_id),
    ).fetchall()
    return {r["reason"]: r["n"] for r in rows}


def _clauses(conn: Connection, hashes: set[str]) -> tuple[dict[str, dict], set[str]]:
    """Cited clause rows by hash, and the hashes no longer the latest text for their rule."""
    rows = conn.execute("SELECT * FROM clauses WHERE text_hash = ANY(%s)", (list(hashes),))
    by_hash = {r["text_hash"]: r for r in rows}
    latest = conn.execute(
        "SELECT DISTINCT ON (source, rule, paragraph) source, rule, paragraph, text_hash"
        " FROM clauses WHERE (source, rule, coalesce(paragraph, 0)) IN"
        "   (SELECT source, rule, coalesce(paragraph, 0) FROM clauses WHERE text_hash = ANY(%s))"
        " ORDER BY source, rule, paragraph, first_seen_at DESC",
        (list(hashes),),
    ).fetchall()
    current = {(r["source"], r["rule"], r["paragraph"]): r["text_hash"] for r in latest}
    superseded = {
        h for h, r in by_hash.items() if current[(r["source"], r["rule"], r["paragraph"])] != h
    }
    return by_hash, superseded


def _ref_key(ref: dict) -> tuple:
    return (ref["source"], ref["rule"], ref["paragraph"])


def audit(conn: Connection, session: int, tenure_id: str) -> Audit | None:
    """The full audit trail of a live segment, or None if it is not live."""
    seg = conn.execute(
        "SELECT ct.run_id, ct.tenure_id, t.input_fingerprint, rc.evaluation_run_id,"
        " rc.ruleset_id, rc.ruleset_version, rc.as_of"
        " FROM current_tenures ct"
        " JOIN tenures t ON t.run_id = ct.run_id AND t.tenure_id = ct.tenure_id"
        " JOIN run_committees rc ON rc.run_id = ct.run_id AND rc.committee_id = ct.committee_id"
        " WHERE ct.session = %s AND ct.tenure_id = %s",
        (session, tenure_id),
    ).fetchone()
    if seg is None:
        return None
    reqs = conn.execute(
        "SELECT r.*, a.derivation, a.evidence, a.clauses FROM requirements r"
        " JOIN requirement_audit a USING (run_id, tenure_id, requirement)"
        " WHERE r.run_id = %s AND r.tenure_id = %s",
        (seg["run_id"], tenure_id),
    ).fetchall()
    hashes = {c["text_hash"] for r in reqs for c in r["clauses"]}
    clause_rows, superseded = _clauses(conn, hashes)
    views = []
    observations: dict[str, set[int]] = {}
    for r in sorted(reqs, key=lambda r: _REQUIREMENT_ORDER[RequirementId(r["requirement"])]):
        cited = {_ref_key(c["ref"]): c["text_hash"] for c in r["clauses"]}
        steps = []
        for s in r["derivation"]:
            cite = None
            if s["clause"] is not None:
                h = cited[_ref_key(s["clause"])]
                ref = s["clause"]
                cite = Cite(
                    short=clause_short(ref["source"], ref["rule"], ref["paragraph"]),
                    long=clause_long(ref["source"], ref["rule"], ref["paragraph"]),
                    text=clause_rows[h]["text"],
                    superseded=h in superseded,
                )
            role = StepRole(s["role"]) if s["role"] else None
            steps.append(
                Step(
                    value=date.fromisoformat(s["value"]) if s["value"] else None,
                    description=s["description"],
                    role=ROLE_TEXT[role] if role else None,
                    is_due=role in DUE_ROLES,
                    cite=cite,
                )
            )
        evidence = []
        for e in r["evidence"]:
            observations.setdefault(e["observation_id"], set())
            if e["row_index"] is not None:
                observations[e["observation_id"]].add(e["row_index"])
            line = e["line"]
            evidence.append(
                EvidenceView(
                    source=line["source"] if line else "Unresolved reference",
                    occurred_on=date.fromisoformat(line["occurred_on"])
                    if line and line["occurred_on"]
                    else None,
                    text=line["text"] if line else "The client could not resolve this record",
                    url=line["url"] if line else None,
                    observation_id=e["observation_id"],
                    row_index=e["row_index"],
                )
            )
        status = RequirementStatus(r["status"])
        views.append(
            RequirementView(
                name=REQUIREMENT_TEXT[RequirementId(r["requirement"])],
                chip_text=CHIP_TEXT[status],
                chip_css=CHIP_CLASS[status],
                reason=REASON_TEXT[ProvisionalReason(r["reason"])] if r["reason"] else None,
                detail=r["detail"],
                due=r["due"],
                resolves_on=r["resolves_on"],
                completed_on=r["completed_on"],
                steps=steps,
                evidence=evidence,
                superseded_rule=any(c["text_hash"] in superseded for c in r["clauses"]),
            )
        )
    history = [
        HistoryEntry(
            changed_at=h["changed_at"],
            from_label=h["from_label"],
            to_label=h["to_label"],
            requirements=[
                REQUIREMENT_TEXT[RequirementId(c["requirement"])].lower()
                for c in h["changed_requirements"]
            ],
        )
        for h in conn.execute(
            "SELECT * FROM label_changes WHERE session = %s AND tenure_id = %s ORDER BY changed_at",
            (session, tenure_id),
        )
    ]
    provenance = Provenance(
        tenure_id=tenure_id,
        push_id=str(seg["run_id"]),
        evaluation_run_id=str(seg["evaluation_run_id"]),
        ruleset=f"{seg['ruleset_id']} v{seg['ruleset_version']}",
        as_of=seg["as_of"],
        input_fingerprint=seg["input_fingerprint"],
        observations=[
            f"{o} (rows {', '.join(map(str, sorted(rows)))})" if rows else o
            for o, rows in sorted(observations.items())
        ],
    )
    return Audit(views, history, provenance)
