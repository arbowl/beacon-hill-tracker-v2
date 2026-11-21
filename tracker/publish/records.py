"""Contract records rebuilt from the detail tables of one committee upload."""

from typing import Any
from uuid import UUID

from beacon_contract import v1
from tracker.db import Connection

_FILTER = (
    "t.run_id = %(run)s AND t.committee_id = %(committee)s"
    " AND (%(bill)s::text IS NULL OR t.bill_id = %(bill)s)"
)


def tenure_dicts(
    conn: Connection, run_id: UUID, committee_id: str, bill_id: str | None = None
) -> list[dict[str, Any]]:
    """Tenures with their full results, in contract shape, ordered by bill; unvalidated."""
    rows = conn.execute(
        "SELECT t.tenure_id, t.bill_id, t.referred_on, t.label, t.input_fingerprint,"
        "   r.requirement, r.obligation_id, r.status, r.reason, r.detail, r.due,"
        "   r.resolves_on, r.completed_on, a.derivation, a.evidence, a.clauses"
        " FROM tenures t"
        " JOIN requirements r ON r.run_id = t.run_id AND r.tenure_id = t.tenure_id"
        " JOIN requirement_audit a ON a.run_id = r.run_id AND a.tenure_id = r.tenure_id"
        "   AND a.requirement = r.requirement"
        f" WHERE {_FILTER}"
        " ORDER BY substring(t.bill_id FROM 2)::int, t.tenure_id, r.requirement",
        {"run": run_id, "committee": committee_id, "bill": bill_id},
    ).fetchall()
    tenures: dict[str, dict[str, Any]] = {}
    for r in rows:
        tenure = tenures.setdefault(
            r["tenure_id"],
            {
                "tenure_id": r["tenure_id"],
                "bill_id": r["bill_id"],
                "committee_id": committee_id,
                "referred_on": r["referred_on"],
                "label": r["label"],
                "input_fingerprint": r["input_fingerprint"],
                "requirements": [],
            },
        )
        tenure["requirements"].append(
            {
                "requirement_id": r["requirement"],
                "obligation_id": r["obligation_id"],
                "status": r["status"],
                "reason": r["reason"],
                "detail": r["detail"],
                "due": r["due"],
                "resolves_on": r["resolves_on"],
                "completed_on": r["completed_on"],
                "derivation": r["derivation"],
                "evidence": r["evidence"],
                "clauses": r["clauses"],
            }
        )
    return list(tenures.values())


def cited_clause_dicts(
    conn: Connection, run_id: UUID, committee_id: str, bill_id: str | None = None
) -> list[dict[str, Any]]:
    """The verbatim rule texts those tenures cite, in contract shape; unvalidated."""
    rows = conn.execute(
        "SELECT c.source, c.rule, c.paragraph, c.text, c.text_hash FROM clauses c"
        " WHERE c.text_hash IN (SELECT cite->>'text_hash' FROM requirement_audit a"
        "   JOIN tenures t ON t.run_id = a.run_id AND t.tenure_id = a.tenure_id,"
        "   jsonb_array_elements(a.clauses) cite"
        f"   WHERE {_FILTER})"
        " ORDER BY c.source, c.rule, c.paragraph NULLS FIRST, c.text_hash",
        {"run": run_id, "committee": committee_id, "bill": bill_id},
    ).fetchall()
    return [
        {
            "ref": {"source": c["source"], "rule": c["rule"], "paragraph": c["paragraph"]},
            "text": c["text"],
            "text_hash": c["text_hash"],
        }
        for c in rows
    ]


def bill_records(
    conn: Connection, run_id: UUID, committee_id: str, bill_id: str
) -> tuple[list[v1.Tenure], list[v1.Clause]]:
    """One bill's tenures in a committee upload, and the rule texts they cite, validated."""
    tenures = [
        v1.Tenure.model_validate(t) for t in tenure_dicts(conn, run_id, committee_id, bill_id)
    ]
    clauses = [
        v1.Clause.model_validate(c) for c in cited_clause_dicts(conn, run_id, committee_id, bill_id)
    ]
    return tenures, clauses
