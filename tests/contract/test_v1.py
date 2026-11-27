"""Every boundary rule of the v1 contract accepts good bundles and rejects bad ones."""

import copy
from collections.abc import Callable
from itertools import product
from typing import Any

import pytest
from pydantic import ValidationError

from beacon_contract.v1 import (
    CommitteeBundle,
    ComplianceLabel,
    RequirementStatus,
    RunManifest,
    UploadMismatch,
    check_upload,
    roll_up,
    text_hash,
)

RULE_TEXT = "Each joint committee shall report on all matters referred to it."
TENURE = "194/H10:J19:2025-02-27"


def _requirement(requirement_id: str, status: str = "Met", **extra: Any) -> dict:
    return {
        "requirement_id": requirement_id,
        "obligation_id": f"{TENURE}:{requirement_id}",
        "status": status,
        "reason": "not_yet_due" if status == "Provisional" else None,
        "detail": "Reported 2025-12-01",
        "due": "2026-01-03",
        "resolves_on": None,
        "completed_on": "2025-12-01",
        "derivation": [
            {
                "description": "Heard",
                "value": "2025-11-04",
                "clause": {"source": "JOINT", "rule": "10", "paragraph": 1},
                "role": "trigger",
            }
        ],
        "evidence": [
            {
                "observation_id": "430a14c1-b7e1-5a0a-8fbf-072cc95ab687",
                "row_index": 0,
                "line": {
                    "source": "Bill history",
                    "occurred_on": "2025-02-27",
                    "text": "House: Referred to the committee on the Judiciary",
                    "url": "https://malegislature.gov/Bills/194/H10",
                },
            },
            {
                "observation_id": "530a14c1-b7e1-5a0a-8fbf-072cc95ab687",
                "row_index": None,
                "line": None,
            },
        ],
        "clauses": [
            {
                "ref": {"source": "JOINT", "rule": "10", "paragraph": 1},
                "text_hash": text_hash(RULE_TEXT),
            }
        ],
    } | extra


def bundle() -> dict:
    return {
        "schema_version": 1,
        "session": 194,
        "evaluation_run_id": "11111111-2222-3333-4444-555555555555",
        "ruleset_id": "s194",
        "ruleset_version": "5",
        "as_of": "2026-11-20T08:31:00+00:00",
        "committee": {
            "committee_id": "J19",
            "name": "Joint Committee on the Judiciary",
            "chamber": "Joint",
            "url": "https://malegislature.gov/Committees/Detail/J19",
        },
        "bills": [
            {"bill_id": "H10", "title": "An Act", "url": "https://malegislature.gov/Bills/194/H10"},
            {"bill_id": "H11", "title": None, "url": "https://malegislature.gov/Bills/194/H11"},
        ],
        "tenures": [
            {
                "tenure_id": TENURE,
                "bill_id": "H10",
                "committee_id": "J19",
                "referred_on": "2025-02-27",
                "label": "Non-Compliant",
                "input_fingerprint": "a" * 64,
                "requirements": [
                    _requirement("report_out_deadline", "Unmet"),
                    _requirement("hearing_notice"),
                    _requirement("summary_posted", "Provisional"),
                ],
            },
            {
                "tenure_id": "194/H11:J19:unobserved",
                "bill_id": "H11",
                "committee_id": "J19",
                "referred_on": None,
                "label": "Provisional",
                "input_fingerprint": "b" * 64,
                "requirements": [
                    {
                        "requirement_id": "tenure_observed",
                        "obligation_id": "194/H11:J19:unobserved:tenure_observed",
                        "status": "Provisional",
                        "reason": "missing_data",
                        "detail": "No referral to J19 found in observed bill history",
                        "due": None,
                        "resolves_on": None,
                        "completed_on": None,
                        "derivation": [],
                        "evidence": [],
                        "clauses": [],
                    }
                ],
            },
        ],
        "clauses": [
            {
                "ref": {"source": "JOINT", "rule": "10", "paragraph": 1},
                "text": RULE_TEXT,
                "text_hash": text_hash(RULE_TEXT),
            }
        ],
        "committee_output": [
            {
                "bill_id": "H4372",
                "title": "New draft",
                "originated_on": "2025-09-08",
                "source_bills": ["H10"],
            }
        ],
    }


def manifest(**extra: Any) -> RunManifest:
    return RunManifest.model_validate(
        {
            "schema_version": 1,
            "push_id": "99999999-2222-3333-4444-555555555555",
            "session": 194,
            "code_version": "0.1.0",
            "created_at": "2026-11-20T09:00:00+00:00",
            "committees": ["J19", "J45"],
        }
        | extra
    )


def _first_tenure(b: dict) -> dict:
    return b["tenures"][0]


def _first_requirement(b: dict) -> dict:
    return b["tenures"][0]["requirements"][0]


def test_valid_bundle_round_trips():
    model = CommitteeBundle.model_validate(bundle())
    again = CommitteeBundle.model_validate_json(model.model_dump_json())
    assert again == model
    assert model.tenures[1].referred_on is None


def _set(path: Callable[[dict], dict], key: str, value: Any) -> Callable[[dict], None]:
    def mutate(b: dict) -> None:
        path(b)[key] = value

    return mutate


def _edit_step_clause(b: dict) -> None:
    _first_requirement(b)["derivation"][0]["clause"]["rule"] = "11"


def _drop_clauses(b: dict) -> None:
    b["clauses"] = []


def _edit_clause_text(b: dict) -> None:
    b["clauses"][0]["text"] = "edited"


def _drop_bill(b: dict) -> None:
    b["bills"] = b["bills"][:1]


def _extra_bill(b: dict) -> None:
    b["bills"].append({"bill_id": "H12", "title": None, "url": "https://x.org/H12"})


def _repeat_requirement(b: dict) -> None:
    reqs = _first_tenure(b)["requirements"]
    reqs.append(copy.deepcopy(reqs[1]))


def _observed_in_dated_tenure(b: dict) -> None:
    _first_tenure(b)["requirements"].append(
        _requirement("tenure_observed", "Provisional") | {"derivation": [], "clauses": []}
    )


def _extra_in_unobserved(b: dict) -> None:
    b["tenures"][1]["requirements"].append(
        _requirement("hearing_notice") | {"obligation_id": "194/H11:J19:unobserved:hearing_notice"}
    )


def _repeat_tenure(b: dict) -> None:
    b["tenures"].append(copy.deepcopy(b["tenures"][0]))


def _other_committee_tenure(b: dict) -> None:
    t = _first_tenure(b)
    t["tenure_id"], t["committee_id"] = "194/H10:J45:2025-02-27", "J45"
    for r in t["requirements"]:
        r["obligation_id"] = f"194/H10:J45:2025-02-27:{r['requirement_id']}"


def _other_session_tenure(b: dict) -> None:
    t = _first_tenure(b)
    t["tenure_id"] = "193/H10:J19:2025-02-27"
    for r in t["requirements"]:
        r["obligation_id"] = f"193/H10:J19:2025-02-27:{r['requirement_id']}"


REJECTIONS: dict[str, Callable[[dict], None]] = {
    "unknown field": _set(lambda b: b, "surprise", 1),
    "schema version": _set(lambda b: b, "schema_version", 2),
    "naive as_of": _set(lambda b: b, "as_of", "2026-11-20T08:31:00"),
    "unknown ruleset": _set(lambda b: b, "ruleset_id", "s195"),
    "committee id form": _set(lambda b: b["committee"], "committee_id", "J019"),
    "chamber mismatch": _set(lambda b: b["committee"], "chamber", "House"),
    "bill id form": _set(lambda b: b["bills"][0], "bill_id", "H.10"),
    "label not rolled up": _set(_first_tenure, "label", "Provisional"),
    "unknown status": _set(_first_requirement, "status", "Failed"),
    "reason without provisional": _set(_first_requirement, "reason", "missing_data"),
    "provisional without reason": _set(
        lambda b: _first_tenure(b)["requirements"][2], "reason", None
    ),
    "malformed tenure id": _set(_first_tenure, "tenure_id", "H10:J19"),
    "tenure id vs bill": _set(_first_tenure, "bill_id", "H11"),
    "tenure id vs referral": _set(_first_tenure, "referred_on", "2025-03-01"),
    "obligation id": _set(_first_requirement, "obligation_id", "elsewhere"),
    "no requirements": _set(_first_tenure, "requirements", []),
    "repeated requirement": _repeat_requirement,
    "tenure_observed on dated tenure": _observed_in_dated_tenure,
    "extra result on unobserved tenure": _extra_in_unobserved,
    "repeated tenure": _repeat_tenure,
    "tenure from another committee": _other_committee_tenure,
    "tenure from another session": _other_session_tenure,
    "bill missing for tenure": _drop_bill,
    "bill without tenure": _extra_bill,
    "cited clause missing": _drop_clauses,
    "clause hash mismatch": _edit_clause_text,
    "derivation clause uncited": _edit_step_clause,
    "fingerprint form": _set(_first_tenure, "input_fingerprint", "xyz"),
    "negative row index": _set(lambda b: _first_requirement(b)["evidence"][0], "row_index", -1),
    "evidence url scheme": _set(
        lambda b: _first_requirement(b)["evidence"][0]["line"], "url", "javascript:alert(1)"
    ),
}


@pytest.mark.parametrize("mutate", REJECTIONS.values(), ids=REJECTIONS.keys())
def test_rejects(mutate: Callable[[dict], None]):
    b = bundle()
    mutate(b)
    with pytest.raises(ValidationError):
        CommitteeBundle.model_validate(b)


def test_manifest_rejects_repeated_and_empty_committees():
    with pytest.raises(ValidationError):
        manifest(committees=["J19", "J19"])
    with pytest.raises(ValidationError):
        manifest(committees=[])


def test_check_upload():
    b = CommitteeBundle.model_validate(bundle())
    check_upload(manifest(), "J19", b)
    with pytest.raises(UploadMismatch):
        check_upload(manifest(), "J45", b)
    with pytest.raises(UploadMismatch):
        check_upload(manifest(committees=["J45"]), "J19", b)
    with pytest.raises(UploadMismatch):
        check_upload(manifest(session=193), "J19", b)


@pytest.mark.parametrize("statuses", list(product(RequirementStatus, repeat=3)))
def test_roll_up(statuses: tuple[RequirementStatus, ...]):
    if RequirementStatus.UNMET in statuses:
        expected = ComplianceLabel.NON_COMPLIANT
    elif all(s == RequirementStatus.MET for s in statuses):
        expected = ComplianceLabel.COMPLIANT
    else:
        expected = ComplianceLabel.PROVISIONAL
    assert roll_up(statuses) == expected


def _carried() -> dict:
    """A bundle with a bill that came before J19 only by accompanying H10."""
    b = bundle()
    b["bills"].append({"bill_id": "H12", "title": None, "url": "https://x.org/H12"})
    b["no_requirements"] = [
        {
            "tenure_id": "194/H12:J19:2025-03-01",
            "bill_id": "H12",
            "committee_id": "J19",
            "referred_on": "2025-03-01",
            "reason": "accompanied",
            "carried_by": "H10",
        }
    ]
    return b


def test_carried_bill_round_trips():
    model = CommitteeBundle.model_validate(_carried())
    (item,) = model.no_requirements
    assert (item.reason.value, item.carried_by) == ("accompanied", "H10")
    assert CommitteeBundle.model_validate_json(model.model_dump_json()) == model


def _carried_item(b: dict) -> dict:
    return b["no_requirements"][0]


CARRIED_REJECTIONS: dict[str, Callable[[dict], None]] = {
    "accompanied without carried_by": _set(_carried_item, "carried_by", None),
    "carried_by with another reason": _set(_carried_item, "reason", "none_apply"),
    "carried by itself": _set(_carried_item, "carried_by", "H12"),
    "carried_by form": _set(_carried_item, "carried_by", "H.10"),
    "accompanied awaiting": _set(_carried_item, "awaiting", ["votes_posted"]),
}


@pytest.mark.parametrize("mutate", CARRIED_REJECTIONS.values(), ids=CARRIED_REJECTIONS.keys())
def test_rejects_carried(mutate: Callable[[dict], None]):
    b = _carried()
    mutate(b)
    with pytest.raises(ValidationError):
        CommitteeBundle.model_validate(b)


def _incomplete() -> dict:
    """A bundle with a bill whose transfer to J19 the record omits."""
    b = _carried()
    b["no_requirements"][0] |= {
        "reason": "record_incomplete",
        "carried_by": None,
        "note": "The bill history does not record its transfer to the committee.",
    }
    return b


def test_record_gap_round_trips():
    model = CommitteeBundle.model_validate(_incomplete())
    (item,) = model.no_requirements
    assert item.reason.value == "record_incomplete"
    assert item.note and item.note.startswith("The bill history")
    assert CommitteeBundle.model_validate_json(model.model_dump_json()) == model


INCOMPLETE_REJECTIONS: dict[str, Callable[[dict], None]] = {
    "record_incomplete without note": _set(_carried_item, "note", None),
    "note with another reason": _set(_carried_item, "reason", "none_apply"),
    "record_incomplete awaiting": _set(_carried_item, "awaiting", ["votes_posted"]),
}


@pytest.mark.parametrize(
    "mutate", INCOMPLETE_REJECTIONS.values(), ids=INCOMPLETE_REJECTIONS.keys()
)
def test_rejects_record_gap(mutate: Callable[[dict], None]):
    b = _incomplete()
    mutate(b)
    with pytest.raises(ValidationError):
        CommitteeBundle.model_validate(b)
