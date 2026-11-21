"""StrEnums for site-only Postgres enum types; data enums come from beacon_contract.v1."""

from enum import StrEnum


class UserRole(StrEnum):
    USER = "user"
    WEBMASTER = "webmaster"


class KeyScope(StrEnum):
    READ = "read"
    INGEST = "ingest"


class WatchKind(StrEnum):
    BILL = "bill"
    COMMITTEE = "committee"


class AlertKind(StrEnum):
    STATUS_CHANGED = "status_changed"
    DEADLINE_APPROACHING = "deadline_approaching"
    DEADLINE_MISSED = "deadline_missed"


class EmailKind(StrEnum):
    WATCH_STATUS_CHANGED = "watch_status_changed"
    WATCH_DEADLINE_APPROACHING = "watch_deadline_approaching"
    WATCH_DEADLINE_MISSED = "watch_deadline_missed"
    WEEKLY_SUMMARY = "weekly_summary"
    ANNOUNCEMENTS = "announcements"


EMAIL_ALERT = {
    EmailKind.WATCH_STATUS_CHANGED: AlertKind.STATUS_CHANGED,
    EmailKind.WATCH_DEADLINE_APPROACHING: AlertKind.DEADLINE_APPROACHING,
    EmailKind.WATCH_DEADLINE_MISSED: AlertKind.DEADLINE_MISSED,
}
ALERT_EMAIL = {alert: kind for kind, alert in EMAIL_ALERT.items()}
SIGNUP_DEFAULTS = frozenset(EMAIL_ALERT)


class DumpFormat(StrEnum):
    CSV_GZ = "csv.gz"
    JSON_GZ = "json.gz"
