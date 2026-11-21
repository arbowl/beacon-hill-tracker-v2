"""View models for a bill segment row, shared by the dashboard and the bill page."""

from dataclasses import dataclass
from typing import Any

from beacon_contract.v1 import ComplianceLabel, RequirementStatus
from tracker.display import (
    CHIP_CLASS,
    CHIP_COLUMN,
    CHIP_ORDER,
    CHIP_PREFIX,
    CHIP_TEXT,
    LABEL_CLASS,
    LABEL_TEXT,
)

NOT_APPLICABLE_TIP = "This requirement has not applied to this bill segment so far"


@dataclass(frozen=True)
class Chip:
    prefix: str
    text: str
    css: str
    tip: str


@dataclass(frozen=True)
class Label:
    text: str
    css: str


def chips(row: dict[str, Any]) -> list[Chip]:
    result = []
    for requirement in CHIP_ORDER:
        column = CHIP_COLUMN[requirement]
        status = RequirementStatus(row[column]) if row[column] is not None else None
        result.append(
            Chip(
                prefix=CHIP_PREFIX[requirement],
                text=CHIP_TEXT[status],
                css=CHIP_CLASS[status],
                tip=row[f"{column}_detail"] or NOT_APPLICABLE_TIP,
            )
        )
    return result


def label(value: str) -> Label:
    lab = ComplianceLabel(value)
    return Label(LABEL_TEXT[lab], LABEL_CLASS[lab])
