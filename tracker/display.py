"""Reader-facing words for internal values. Templates use only these."""

from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from beacon_contract.v1 import (
    ChangeKind,
    ComplianceLabel,
    EvidenceSource,
    ProvisionalReason,
    RequirementId,
    RequirementStatus,
    RuleSource,
    StepRole,
)
from tracker.enums import EMAIL_ALERT, AlertKind, EmailKind

LEGISLATURE_TZ = ZoneInfo("America/New_York")


class StatusFilter(StrEnum):
    """URL values for the status filter; they name labels, not display text."""

    COMPLIANT = "compliant"
    PROVISIONAL = "provisional"
    NON_COMPLIANT = "non-compliant"


FILTER_LABEL = {
    StatusFilter.COMPLIANT: ComplianceLabel.COMPLIANT,
    StatusFilter.PROVISIONAL: ComplianceLabel.PROVISIONAL,
    StatusFilter.NON_COMPLIANT: ComplianceLabel.NON_COMPLIANT,
}
LABEL_FILTER = {label: f for f, label in FILTER_LABEL.items()}

LABEL_TEXT = {
    ComplianceLabel.COMPLIANT: "No violation detected",
    ComplianceLabel.PROVISIONAL: "Provisional",
    ComplianceLabel.NON_COMPLIANT: "Violation detected",
}
LABEL_CLASS = {
    ComplianceLabel.COMPLIANT: "compliant",
    ComplianceLabel.PROVISIONAL: "provisional",
    ComplianceLabel.NON_COMPLIANT: "noncompliant",
}
LABEL_SEVERITY = {
    ComplianceLabel.NON_COMPLIANT: 0,
    ComplianceLabel.PROVISIONAL: 1,
    ComplianceLabel.COMPLIANT: 2,
}

CHIP_TEXT = {
    RequirementStatus.MET: "Met",
    RequirementStatus.PROVISIONAL: "Pending",
    RequirementStatus.UNMET: "Not shown",
    None: "Not applicable",
}
CHIP_CLASS = {
    RequirementStatus.MET: "met",
    RequirementStatus.PROVISIONAL: "prov",
    RequirementStatus.UNMET: "unmet",
    None: "na",
}

REQUIREMENT_TEXT = {
    RequirementId.HEARING_NOTICE: "Hearing notice",
    RequirementId.SUMMARY_POSTED: "Summary",
    RequirementId.VOTES_POSTED: "Vote",
    RequirementId.REPORT_OUT_DEADLINE: "Reported out",
    RequirementId.TENURE_OBSERVED: "Referral found",
}
CHIP_PREFIX = {
    RequirementId.HEARING_NOTICE: "Notice",
    RequirementId.SUMMARY_POSTED: "Summary",
    RequirementId.VOTES_POSTED: "Vote",
    RequirementId.REPORT_OUT_DEADLINE: "Reported",
}
CHIP_ORDER = tuple(CHIP_PREFIX)
CHIP_COLUMN = {
    RequirementId.HEARING_NOTICE: "hearing_notice",
    RequirementId.SUMMARY_POSTED: "summary_posted",
    RequirementId.VOTES_POSTED: "votes_posted",
    RequirementId.REPORT_OUT_DEADLINE: "report_out",
}

REASON_TEXT = {
    ProvisionalReason.NOT_YET_DUE: "Not yet due",
    ProvisionalReason.AMBIGUOUS_TIMING: "Timing is ambiguous",
    ProvisionalReason.MISSING_DATA: "Data needed is missing",
    ProvisionalReason.AWAITING_REVIEW: "Awaiting review",
}

ROLE_TEXT = {
    StepRole.TRIGGER: "Trigger",
    StepRole.BASE_DUE: "Deadline",
    StepRole.EXTENSION: "Extension",
    StepRole.EXTENSION_IGNORED: "Extension not applied",
    StepRole.CAP: "Cap",
    StepRole.TOLERANCE: "Tolerance",
}
DUE_ROLES = frozenset({StepRole.BASE_DUE, StepRole.EXTENSION, StepRole.CAP})

SOURCE_ABBREV = {RuleSource.JOINT: "JR", RuleSource.HOUSE: "HR", RuleSource.SENATE: "SR"}
SOURCE_NAME = {
    RuleSource.JOINT: "Joint Rule",
    RuleSource.HOUSE: "House Rule",
    RuleSource.SENATE: "Senate Rule",
}
EVIDENCE_TEXT = {s: s.value for s in EvidenceSource}
CHANGE_KIND_TEXT = {k: k.value for k in ChangeKind}
EMAIL_KIND_TEXT = {
    EmailKind.WATCH_STATUS_CHANGED: "A bill segment I watch changes status",
    EmailKind.WATCH_DEADLINE_APPROACHING: "A deadline on something I watch is 7 days away",
    EmailKind.WATCH_DEADLINE_MISSED: "A deadline on something I watch is missed",
    EmailKind.WEEKLY_SUMMARY: "Weekly compliance summary",
    EmailKind.ANNOUNCEMENTS: "Occasional announcements about the Tracker",
}
EMAIL_KIND_SHORT = {
    EmailKind.WATCH_STATUS_CHANGED: "status change alerts",
    EmailKind.WATCH_DEADLINE_APPROACHING: "deadline reminders",
    EmailKind.WATCH_DEADLINE_MISSED: "missed deadline alerts",
    EmailKind.WEEKLY_SUMMARY: "the weekly summary",
    EmailKind.ANNOUNCEMENTS: "announcements",
}
EMAIL_KIND_NOTE = {
    EmailKind.WEEKLY_SUMMARY: "The summary figures, the week's changes, and current highlights.",
    EmailKind.ANNOUNCEMENTS: "A few times a year at most.",
}
_EMAIL_KIND_GROUPS = (
    ("Alerts for things you watch", tuple(EMAIL_ALERT)),
    ("Other email", (EmailKind.WEEKLY_SUMMARY, EmailKind.ANNOUNCEMENTS)),
)
ALERT_KIND_TEXT = {
    AlertKind.STATUS_CHANGED: "Status",
    AlertKind.DEADLINE_APPROACHING: "Deadline soon",
    AlertKind.DEADLINE_MISSED: "Missed",
}


def clause_short(source: str, rule: str, paragraph: int | None) -> str:
    base = f"{SOURCE_ABBREV[RuleSource(source)]} {rule}"
    return base if paragraph is None else f"{base} para {paragraph}"


def clause_long(source: str, rule: str, paragraph: int | None) -> str:
    base = f"{SOURCE_NAME[RuleSource(source)]} {rule}"
    return base if paragraph is None else f"{base}, paragraph {paragraph}"


def moment(value: datetime) -> str:
    """'Nov 20, 2026, 3:31 AM ET'."""
    local = value.astimezone(LEGISLATURE_TZ)
    hour = local.strftime("%I").lstrip("0")
    return f"{local:%b} {local.day}, {local.year}, {hour}:{local:%M} {local:%p} ET"


def day(value: date | datetime) -> str:
    """ISO date; instants are converted to the Legislature's time zone first."""
    if isinstance(value, datetime):
        value = value.astimezone(LEGISLATURE_TZ).date()
    return value.isoformat()


def number(value: int) -> str:
    return f"{value:,}"


def file_size(value: int) -> str:
    """1234567 -> '1.2 MB'."""
    for unit, scale in (("MB", 1_000_000), ("KB", 1_000)):
        if value >= scale:
            return f"{value / scale:.1f} {unit}"
    return f"{value} bytes"


def rounded_down_thousands(value: int) -> str:
    """11,204 -> '11,000+'; below 1,000 the exact count."""
    return f"{value // 1000 * 1000:,}+" if value >= 1000 else f"{value:,}"


def email_kind_groups() -> list[tuple[str, list[tuple[EmailKind, str, str | None]]]]:
    """Email kinds for preference forms, as (legend, [(kind, label, note)])."""
    return [
        (legend, [(k, EMAIL_KIND_TEXT[k], EMAIL_KIND_NOTE.get(k)) for k in kinds])
        for legend, kinds in _EMAIL_KIND_GROUPS
    ]
