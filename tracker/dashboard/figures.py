"""Summary figures, deltas, chart data, and the committee range strip."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median_low
from typing import Any

from tracker.db import Connection

MIN_DETERMINED = 20
FUNNEL_STAGES = (
    "Tracked",
    "Notice met or n/a",
    "+ Summary",
    "+ Vote",
    "+ Reported on time",
)


@dataclass(frozen=True)
class Delta:
    points: Decimal
    period: str

    @property
    def direction(self) -> str:
        return "up" if self.points > 0 else "down" if self.points < 0 else "flat"


def _rate(row: dict[str, Any]) -> Decimal | None:
    if not row["total"]:
        return None
    share = Decimal(row["compliant"] + row["provisional"]) / row["total"] * 100
    return share.quantize(Decimal("0.01"))


def deltas(
    conn: Connection, session: int, committee_id: str, current: Decimal | None, now: datetime
) -> list[Delta]:
    """Rate change against the newest snapshot at least 24 hours and 7 days old."""
    if current is None:
        return []
    found = []
    for period, age in (("today", timedelta(hours=24)), ("7d", timedelta(days=7))):
        row = conn.execute(
            "SELECT total, compliant, provisional FROM snapshots"
            " WHERE session = %s AND committee_id = %s AND taken_at <= %s"
            " ORDER BY taken_at DESC LIMIT 1",
            (session, committee_id, now - age),
        ).fetchone()
        then = _rate(row) if row else None
        if then is not None:
            found.append(Delta((current - then).quantize(Decimal("0.1")), period))
    return found


def chart_data(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "breakdown": {
            "labels": ["No violation detected", "Provisional", "Violation detected"],
            "values": [stats["compliant"], stats["provisional"], stats["non_compliant"]],
            "filters": ["compliant", "provisional", "non-compliant"],
        },
        "funnel": {"labels": list(FUNNEL_STAGES), "values": list(stats["funnel"])},
    }


@dataclass(frozen=True)
class StripDot:
    committee_id: str
    name: str
    rate: Decimal
    cx: float
    cy: int
    selected: bool


@dataclass(frozen=True)
class Strip:
    dots: list[StripDot]
    median: Decimal
    median_x: float
    ticks: list[tuple[int, float]]
    low: Decimal
    high: Decimal


_W, _LEFT, _RIGHT, _BASE, _R, _STEP = 360, 12, 12, 92, 6, 14


def _x(rate: Decimal) -> float:
    return round(_LEFT + (_W - _LEFT - _RIGHT) * float(rate) / 100, 1)


def strip(conn: Connection, session: int, selected: str | None) -> Strip | None:
    """One dot per committee with enough determined segments; coincident dots stack."""
    rows = conn.execute(
        "SELECT s.committee_id, c.name, s.decided_rate FROM committee_stats s"
        " JOIN committees c USING (session, committee_id)"
        " WHERE s.session = %s AND s.compliant + s.non_compliant >= %s"
        " ORDER BY s.decided_rate, c.name",
        (session, MIN_DETERMINED),
    ).fetchall()
    if not rows:
        return None
    placed: list[tuple[float, int]] = []
    dots = []
    for r in rows:
        cx, level = _x(r["decided_rate"]), 0
        while any(abs(cx - px) < 2 * _R + 1 and py == _BASE - level * _STEP for px, py in placed):
            level += 1
        cy = _BASE - level * _STEP
        placed.append((cx, cy))
        dots.append(
            StripDot(
                r["committee_id"],
                r["name"],
                r["decided_rate"],
                cx,
                cy,
                r["committee_id"] == selected,
            )
        )
    rates = [r["decided_rate"] for r in rows]
    med = median_low(rates)
    return Strip(
        dots=dots,
        median=med,
        median_x=_x(med),
        ticks=[(t, _x(Decimal(t))) for t in (0, 25, 50, 75, 100)],
        low=min(rates),
        high=max(rates),
    )
