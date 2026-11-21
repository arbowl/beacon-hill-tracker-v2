"""Response models for the public API. The OpenAPI document is generated from these.

Labels and statuses use the data's own values (Compliant, Provisional, Non-Compliant; Met,
Provisional, Unmet). The Methodology page maps them to the labels shown on the site.
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from beacon_contract import v1


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class LabelCounts(_Model):
    compliant: int
    provisional: int
    non_compliant: int


class Stats(_Model):
    """Summary figures for a session, or for one committee in it. Counts are bill segments
    unless named for bills."""

    session: int
    committee_id: str | None = Field(description="null for the whole session")
    as_of: datetime = Field(description="When the newest data in these figures was evaluated")
    segments: int
    bills: int
    bills_with_violation: int = Field(description="Bills with at least one Non-Compliant segment")
    labels: LabelCounts
    compliance_rate: float | None = Field(
        description="Percent of bill segments that are Compliant or Provisional"
    )
    decided_rate: float | None = Field(
        description="Compliant as a percent of Compliant plus Non-Compliant segments"
    )
    hearing_notice_total: int = Field(description="Segments with a hearing-notice obligation")
    hearing_notice_unmet: int
    summary_unmet: int
    votes_unmet: int
    report_out_unmet: int
    due_next_14_days: int = Field(description="Provisional segments with a deadline in 14 days")

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Stats":
        return cls(
            session=row["session"],
            committee_id=None if row["committee_id"] == "*" else row["committee_id"],
            as_of=row["as_of"],
            segments=row["total"],
            bills=row["bills_total"],
            bills_with_violation=row["bills_with_violation"],
            labels=LabelCounts(
                compliant=row["compliant"],
                provisional=row["provisional"],
                non_compliant=row["non_compliant"],
            ),
            compliance_rate=_rate(row["compliance_rate"]),
            decided_rate=_rate(row["decided_rate"]),
            hearing_notice_total=row["hearing_notice_total"],
            hearing_notice_unmet=row["hearing_notice_unmet"],
            summary_unmet=row["summary_unmet"],
            votes_unmet=row["votes_unmet"],
            report_out_unmet=row["report_out_unmet"],
            due_next_14_days=row["due_next_14_days"],
        )


def _rate(value: Any) -> float | None:
    return None if value is None else float(value)


class CommitteeItem(_Model):
    committee_id: str
    name: str
    chamber: v1.Chamber
    as_of: datetime = Field(description="When this committee's data was evaluated")
    url: str = Field(description="The dashboard filtered to this committee")


class CommitteeList(_Model):
    session: int
    committees: list[CommitteeItem]


class CommitteeDetail(_Model):
    committee: CommitteeItem
    stats: Stats


class SegmentItem(_Model):
    """One bill segment: a bill as one committee handled it. A null requirement status means
    no obligation arose."""

    tenure_id: str
    bill_id: str
    title: str | None
    committee_id: str
    committee_name: str
    referred_on: date | None
    referral_observed: bool
    label: v1.ComplianceLabel
    hearing_notice: v1.RequirementStatus | None
    summary_posted: v1.RequirementStatus | None
    votes_posted: v1.RequirementStatus | None
    report_out_deadline: v1.RequirementStatus | None
    report_due: date | None
    next_due: date | None = Field(description="Earliest due date of a Provisional requirement")
    url: str = Field(description="The bill's page on the site")


class SegmentPage(_Model):
    session: int
    total: int = Field(description="Segments matching the filters, across all pages")
    next_cursor: str | None = Field(description="Pass as after= for the next page")
    segments: list[SegmentItem]


class BillSegment(_Model):
    segment: SegmentItem
    tenure: v1.Tenure = Field(description="Full results with derivation steps and evidence")


class BillDetail(_Model):
    session: int
    bill_id: str
    title: str | None
    url: str
    segments: list[BillSegment]
    clauses: list[v1.Clause] = Field(description="Verbatim rule text the results cite")
