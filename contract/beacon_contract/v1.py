"""Schema version 1 of the push from the beacon client to the Tracker site."""

import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    model_validator,
)

SCHEMA_VERSION = 1

BILL_ID_PATTERN = r"^[HS][1-9]\d*$"
COMMITTEE_ID_PATTERN = r"^[HSJ][1-9]\d*$"
UNOBSERVED = "unobserved"
_TENURE_ID = re.compile(
    r"^(?P<session>[1-9]\d*)/(?P<bill>[HS][1-9]\d*):(?P<committee>[HSJ][1-9]\d*)"
    rf":(?P<referral>\d{{4}}-\d{{2}}-\d{{2}}|{UNOBSERVED})$"
)

BillId = Annotated[str, StringConstraints(pattern=BILL_ID_PATTERN)]
CommitteeId = Annotated[str, StringConstraints(pattern=COMMITTEE_ID_PATTERN)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, StringConstraints(min_length=1)]


class Chamber(StrEnum):
    HOUSE = "House"
    SENATE = "Senate"
    JOINT = "Joint"


_CHAMBER_PREFIX = {"H": Chamber.HOUSE, "S": Chamber.SENATE, "J": Chamber.JOINT}


class RuleSource(StrEnum):
    JOINT = "JOINT"
    HOUSE = "HOUSE"
    SENATE = "SENATE"


class RequirementId(StrEnum):
    HEARING_NOTICE = "hearing_notice"
    SUMMARY_POSTED = "summary_posted"
    VOTES_POSTED = "votes_posted"
    REPORT_OUT_DEADLINE = "report_out_deadline"
    TENURE_OBSERVED = "tenure_observed"


class RequirementStatus(StrEnum):
    MET = "Met"
    UNMET = "Unmet"
    PROVISIONAL = "Provisional"


class ProvisionalReason(StrEnum):
    NOT_YET_DUE = "not_yet_due"
    AMBIGUOUS_TIMING = "ambiguous_timing"
    MISSING_DATA = "missing_data"
    AWAITING_REVIEW = "awaiting_review"


class ComplianceLabel(StrEnum):
    COMPLIANT = "Compliant"
    NON_COMPLIANT = "Non-Compliant"
    PROVISIONAL = "Provisional"


class NoRequirementsReason(StrEnum):
    NONE_APPLY = "none_apply"
    NONE_YET = "none_yet"
    ACCOMPANIED = "accompanied"
    RECORD_INCOMPLETE = "record_incomplete"


class StepRole(StrEnum):
    TRIGGER = "trigger"
    BASE_DUE = "base_due"
    EXTENSION = "extension"
    EXTENSION_IGNORED = "extension_ignored"
    CAP = "cap"
    TOLERANCE = "tolerance"


class EvidenceSource(StrEnum):
    HISTORY = "Bill history"
    SEARCH = "Document search"
    DOCUMENT = "Document"
    HEARING = "Hearing page"
    LISTING = "Committee bill list"


RULESET_REQUIREMENTS: dict[str, frozenset[RequirementId]] = {
    "s194": frozenset(
        {
            RequirementId.HEARING_NOTICE,
            RequirementId.SUMMARY_POSTED,
            RequirementId.VOTES_POSTED,
            RequirementId.REPORT_OUT_DEADLINE,
        }
    ),
}


def roll_up(statuses: Iterable[RequirementStatus]) -> ComplianceLabel:
    """Any Unmet -> Non-Compliant; all Met -> Compliant; otherwise Provisional."""
    found = set(statuses)
    if RequirementStatus.UNMET in found:
        return ComplianceLabel.NON_COMPLIANT
    if found == {RequirementStatus.MET}:
        return ComplianceLabel.COMPLIANT
    return ComplianceLabel.PROVISIONAL


def text_hash(text: str) -> str:
    """sha256 hex of UTF-8 text, as the client computes clause hashes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _duplicates(values: Iterable[object]) -> list[str]:
    return sorted(str(v) for v, n in Counter(values).items() if n > 1)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RunManifest(_Model):
    """Opens a push: the committees it will upload before publishing."""

    schema_version: Literal[1]
    push_id: UUID
    session: PositiveInt
    code_version: Text
    created_at: AwareDatetime
    committees: tuple[CommitteeId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_committees(self) -> Self:
        if dupes := _duplicates(self.committees):
            raise ValueError(f"committees listed more than once: {dupes}")
        return self


class Committee(_Model):
    committee_id: CommitteeId
    name: Text
    chamber: Chamber
    url: HttpUrl

    @model_validator(mode="after")
    def _chamber_matches_id(self) -> Self:
        if _CHAMBER_PREFIX[self.committee_id[0]] != self.chamber:
            raise ValueError(f"chamber {self.chamber} does not match {self.committee_id}")
        return self


class Bill(_Model):
    bill_id: BillId
    title: str | None
    url: HttpUrl


class ClauseRef(_Model):
    source: RuleSource
    rule: Text
    paragraph: PositiveInt | None


class Clause(_Model):
    """Verbatim rule text, addressed by its hash."""

    ref: ClauseRef
    text: Text
    text_hash: Sha256Hex

    @model_validator(mode="after")
    def _hash_matches_text(self) -> Self:
        if text_hash(self.text) != self.text_hash:
            raise ValueError("text_hash is not the sha256 of text")
        return self


class ClauseCitation(_Model):
    ref: ClauseRef
    text_hash: Sha256Hex


class DerivationStep(_Model):
    description: Text
    value: date | None
    clause: ClauseRef | None
    role: StepRole | None


class EvidenceLine(_Model):
    """An evidence reference as the client resolved it to source text."""

    source: EvidenceSource
    occurred_on: date | None
    text: Text
    url: HttpUrl | None


class Evidence(_Model):
    """Points into the client store; line is None if the client could not resolve it."""

    observation_id: UUID
    row_index: NonNegativeInt | None
    line: EvidenceLine | None


class RequirementResult(_Model):
    requirement_id: RequirementId
    obligation_id: Text
    status: RequirementStatus
    reason: ProvisionalReason | None
    detail: Text
    due: date | None
    resolves_on: date | None
    completed_on: date | None
    derivation: tuple[DerivationStep, ...]
    evidence: tuple[Evidence, ...]
    clauses: tuple[ClauseCitation, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.status == RequirementStatus.PROVISIONAL) != (self.reason is not None):
            raise ValueError("reason is required if and only if status is Provisional")
        cited = [c.ref for c in self.clauses]
        if dupes := _duplicates(cited):
            raise ValueError(f"clauses cited more than once: {dupes}")
        uncited = {s.clause for s in self.derivation if s.clause} - set(cited)
        if uncited:
            names = sorted(f"{r.source} {r.rule}.{r.paragraph}" for r in uncited)
            raise ValueError(f"derivation refers to uncited clauses: {names}")
        return self


class Tenure(_Model):
    """One referral of a bill to a committee (a "bill segment" on the site).

    requirements holds one result per obligation that arose; a requirement with
    no obligation for this tenure is absent. An unobserved tenure has no referral
    date and carries only the tenure_observed result.
    """

    tenure_id: Text
    bill_id: BillId
    committee_id: CommitteeId
    referred_on: date | None
    label: ComplianceLabel
    input_fingerprint: Sha256Hex
    requirements: tuple[RequirementResult, ...] = Field(min_length=1)

    @property
    def session(self) -> int:
        match = _TENURE_ID.match(self.tenure_id)
        assert match is not None
        return int(match["session"])

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        match = _TENURE_ID.match(self.tenure_id)
        if match is None:
            raise ValueError(f"malformed tenure_id {self.tenure_id!r}")
        if (match["bill"], match["committee"]) != (self.bill_id, self.committee_id):
            raise ValueError("tenure_id does not match bill_id and committee_id")
        unobserved = match["referral"] == UNOBSERVED
        referral = None if unobserved else date.fromisoformat(match["referral"])
        if referral != self.referred_on:
            raise ValueError("tenure_id does not match referred_on")
        ids = [r.requirement_id for r in self.requirements]
        if dupes := _duplicates(ids):
            raise ValueError(f"requirements repeated: {dupes}")
        if unobserved != (RequirementId.TENURE_OBSERVED in ids):
            raise ValueError("tenure_observed belongs to, and only to, unobserved tenures")
        if unobserved and len(ids) != 1:
            raise ValueError("an unobserved tenure carries only tenure_observed")
        for r in self.requirements:
            if r.obligation_id != f"{self.tenure_id}:{r.requirement_id}":
                raise ValueError(f"obligation_id {r.obligation_id!r} does not match")
        expected = roll_up(r.status for r in self.requirements)
        if self.label != expected:
            raise ValueError(f"label {self.label} does not roll up; expected {expected}")
        return self


class NoRequirements(_Model):
    """A tenure that owed nothing as of the bundle's as_of: it has no label and counts
    toward no rate. awaiting names the requirements that will arise when the committee
    acts; reason is none_yet if and only if it is non-empty. carried_by names the bill
    (a study order or new draft) this one accompanied before the committee, which is
    judged in its place; reason is accompanied if and only if it is set, and
    referred_on is then the day the bill came before the committee with it. note
    explains a reviewed gap in the legislative record, which does not show how the bill
    came before the committee; reason is record_incomplete if and only if it is set, and
    referred_on is then the day the bill must have come before the committee.
    """

    tenure_id: Text
    bill_id: BillId
    committee_id: CommitteeId
    referred_on: date
    reason: NoRequirementsReason
    awaiting: tuple[RequirementId, ...] = ()
    carried_by: BillId | None = None
    note: Text | None = None

    @property
    def session(self) -> int:
        match = _TENURE_ID.match(self.tenure_id)
        assert match is not None
        return int(match["session"])

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        match = _TENURE_ID.match(self.tenure_id)
        if match is None:
            raise ValueError(f"malformed tenure_id {self.tenure_id!r}")
        if (match["bill"], match["committee"]) != (self.bill_id, self.committee_id):
            raise ValueError("tenure_id does not match bill_id and committee_id")
        if match["referral"] != self.referred_on.isoformat():
            raise ValueError("tenure_id does not match referred_on")
        if dupes := _duplicates(self.awaiting):
            raise ValueError(f"awaiting repeated: {dupes}")
        if RequirementId.TENURE_OBSERVED in self.awaiting:
            raise ValueError("tenure_observed cannot be awaited")
        if (self.reason == NoRequirementsReason.NONE_YET) != bool(self.awaiting):
            raise ValueError("reason is none_yet if and only if a requirement is awaited")
        if (self.reason == NoRequirementsReason.ACCOMPANIED) != (self.carried_by is not None):
            raise ValueError("reason is accompanied if and only if carried_by is set")
        if self.carried_by == self.bill_id:
            raise ValueError("a bill cannot be carried by itself")
        if (self.reason == NoRequirementsReason.RECORD_INCOMPLETE) != (self.note is not None):
            raise ValueError("reason is record_incomplete if and only if note is set")
        return self


class CommitteeOutput(_Model):
    """A document the committee produced, with the bills it came from."""

    bill_id: BillId
    title: str | None
    originated_on: date
    source_bills: tuple[BillId, ...]


class CommitteeBundle(_Model):
    """One committee's latest evaluation run, self-contained."""

    schema_version: Literal[1]
    session: PositiveInt
    evaluation_run_id: UUID
    ruleset_id: Text
    ruleset_version: Text
    as_of: AwareDatetime
    committee: Committee
    bills: tuple[Bill, ...]
    tenures: tuple[Tenure, ...]
    clauses: tuple[Clause, ...]
    committee_output: tuple[CommitteeOutput, ...] = ()
    no_requirements: tuple[NoRequirements, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        allowed = RULESET_REQUIREMENTS.get(self.ruleset_id)
        if allowed is None:
            raise ValueError(f"unknown ruleset {self.ruleset_id!r}")
        allowed = allowed | {RequirementId.TENURE_OBSERVED}
        for field, values in (
            ("bills", [b.bill_id for b in self.bills]),
            (
                "tenures",
                [t.tenure_id for t in self.tenures] + [n.tenure_id for n in self.no_requirements],
            ),
            ("clauses", [c.text_hash for c in self.clauses]),
        ):
            if dupes := _duplicates(values):
                raise ValueError(f"{field} repeated: {dupes}")
        committee_id = self.committee.committee_id
        clause_refs = {c.text_hash: c.ref for c in self.clauses}
        for t in self.tenures:
            if t.committee_id != committee_id or t.session != self.session:
                raise ValueError(f"{t.tenure_id} is not a {self.session}/{committee_id} tenure")
            for r in t.requirements:
                if r.requirement_id not in allowed:
                    raise ValueError(f"{r.requirement_id} is not in ruleset {self.ruleset_id}")
                for c in r.clauses:
                    if clause_refs.get(c.text_hash) != c.ref:
                        raise ValueError(f"{t.tenure_id} cites a clause missing from clauses")
        for n in self.no_requirements:
            if n.committee_id != committee_id or n.session != self.session:
                raise ValueError(f"{n.tenure_id} is not a {self.session}/{committee_id} tenure")
            for awaited in n.awaiting:
                if awaited not in allowed:
                    raise ValueError(f"{awaited} is not in ruleset {self.ruleset_id}")
        referenced = {t.bill_id for t in self.tenures} | {n.bill_id for n in self.no_requirements}
        listed = {b.bill_id for b in self.bills}
        if referenced != listed:
            raise ValueError(
                f"bills must match tenures; missing {sorted(referenced - listed)}, "
                f"unused {sorted(listed - referenced)}"
            )
        return self


class UploadMismatch(ValueError):
    """A valid bundle uploaded to the wrong place."""


def check_upload(manifest: RunManifest, path_committee_id: str, bundle: CommitteeBundle) -> None:
    """Raise UploadMismatch unless the bundle belongs at this path of this push."""
    committee_id = bundle.committee.committee_id
    if path_committee_id != committee_id:
        raise UploadMismatch(f"path names {path_committee_id}, bundle is {committee_id}")
    if committee_id not in manifest.committees:
        raise UploadMismatch(f"{committee_id} is not in this push's committees")
    if bundle.session != manifest.session:
        raise UploadMismatch(f"bundle session {bundle.session} != push session {manifest.session}")


class RunStatus(StrEnum):
    OPEN = "open"
    PUBLISHED = "published"
    ABANDONED = "abandoned"
    PRUNED = "pruned"


class RunState(_Model):
    """The site's reply to every ingest call that succeeds."""

    push_id: UUID
    status: RunStatus
    committees: tuple[CommitteeId, ...]
    uploaded: tuple[CommitteeId, ...]
    opened_at: AwareDatetime
    published_at: AwareDatetime | None


SEMVER_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
SemVer = Annotated[str, StringConstraints(pattern=SEMVER_PATTERN)]
ChangeItem = Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class ChangeKind(StrEnum):
    """Keep a Changelog section headings."""

    ADDED = "Added"
    CHANGED = "Changed"
    DEPRECATED = "Deprecated"
    REMOVED = "Removed"
    FIXED = "Fixed"
    SECURITY = "Security"


class ChangeSection(_Model):
    kind: ChangeKind
    items: tuple[ChangeItem, ...] = Field(min_length=1, max_length=100)


class Release(_Model):
    """One released version of the client. Items are plain text, shown verbatim."""

    version: SemVer
    released_on: date
    sections: tuple[ChangeSection, ...] = Field(min_length=1)

    @property
    def semver(self) -> tuple[int, int, int]:
        major, minor, patch = self.version.split(".")
        return int(major), int(minor), int(patch)

    @model_validator(mode="after")
    def _unique_kinds(self) -> Self:
        if dupes := _duplicates(s.kind for s in self.sections):
            raise ValueError(f"sections repeated: {dupes}")
        return self


class Changelog(_Model):
    """Every released version of the client, newest first; Unreleased is never sent.

    code_version is the version of the client sending it and must be the newest release.
    """

    schema_version: Literal[1]
    code_version: SemVer
    releases: tuple[Release, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if dupes := _duplicates(r.version for r in self.releases):
            raise ValueError(f"versions repeated: {dupes}")
        for newer, older in zip(self.releases, self.releases[1:], strict=False):
            if newer.semver <= older.semver:
                raise ValueError(f"releases not newest first: {newer.version}, {older.version}")
            if newer.released_on < older.released_on:
                raise ValueError(f"{newer.version} is dated before {older.version}")
        if self.releases[0].version != self.code_version:
            raise ValueError(
                f"code_version {self.code_version} is not the newest release"
                f" {self.releases[0].version}"
            )
        return self


class ChangelogState(_Model):
    """The site's reply to a changelog upload."""

    releases: tuple[SemVer, ...]
    updated: tuple[SemVer, ...]


class ErrorDetail(_Model):
    loc: tuple[str | int, ...]
    msg: str


class ErrorInfo(_Model):
    code: Text
    message: Text
    details: tuple[ErrorDetail, ...] = ()


class ErrorBody(_Model):
    """The site's reply to every ingest call that fails."""

    error: ErrorInfo
