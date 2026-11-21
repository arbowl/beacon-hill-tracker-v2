"""Client releases as currently shown."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from beacon_contract import v1
from tracker.db import Connection

NOTICE_DAYS = 14

_SHOWN = (
    "SELECT * FROM (SELECT DISTINCT ON (version) version, content_hash, major, minor, patch,"
    " released_on, sections, current_since, count(*) OVER (PARTITION BY version) AS texts"
    " FROM changelog_releases ORDER BY version, current_since DESC) shown"
)
_NEWEST_FIRST = " ORDER BY major DESC, minor DESC, patch DESC"


@dataclass(frozen=True)
class ShownRelease:
    """corrected_at is set when the release's text was replaced after it was first received."""

    version: str
    content_hash: str
    released_on: date
    sections: tuple[v1.ChangeSection, ...]
    corrected_at: datetime | None


def _release(row: dict) -> ShownRelease:
    return ShownRelease(
        version=row["version"],
        content_hash=row["content_hash"],
        released_on=row["released_on"],
        sections=tuple(v1.ChangeSection.model_validate(s) for s in row["sections"]),
        corrected_at=row["current_since"] if row["texts"] > 1 else None,
    )


def releases(conn: Connection) -> list[ShownRelease]:
    return [_release(r) for r in conn.execute(_SHOWN + _NEWEST_FIRST).fetchall()]


def notice(conn: Connection, today: date) -> ShownRelease | None:
    """The newest major or minor release of the last NOTICE_DAYS days. Patches change no finding."""
    row = conn.execute(
        _SHOWN + " WHERE patch = 0 AND released_on > %s" + _NEWEST_FIRST + " LIMIT 1",
        (today - timedelta(days=NOTICE_DAYS),),
    ).fetchone()
    return _release(row) if row else None
