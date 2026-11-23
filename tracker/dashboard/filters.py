"""Dashboard query parameters."""

import base64
import json
import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Self
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tracker.display import StatusFilter

_BILL_QUERY = re.compile(r"^\s*([HS])\s*\.?\s*(\d+)\s*$", re.I)
PAGE_SIZE = 50

# The committee filter also takes a chamber letter, selecting all of that chamber's committees.
CHAMBERS = {"H": "House", "S": "Senate", "J": "Joint"}
COMMITTEE_OR_CHAMBER = r"^[HSJ]([1-9]\d*)?$"


class Sort(StrEnum):
    SEVERITY = "severity"
    BILL = "bill"
    DUE = "due"


SORT_TEXT = {
    Sort.SEVERITY: "Most severe first",
    Sort.BILL: "Bill number",
    Sort.DUE: "Next deadline",
}

# Keyset columns per sort, all ascending; the last is unique.
SORT_KEYS: dict[Sort, tuple[str, ...]] = {
    Sort.SEVERITY: ("severity", "due_key", "bill_num", "bill_id", "tenure_id"),
    Sort.BILL: ("bill_num", "bill_id", "tenure_id"),
    Sort.DUE: ("due_key", "bill_num", "bill_id", "tenure_id"),
}
_KEY_TYPES = {
    "severity": int,
    "due_key": date.fromisoformat,
    "bill_num": int,
    "bill_id": str,
    "tenure_id": str,
}


def normalize_bill_id(text: str) -> str | None:
    """'h.10' -> 'H10'; None if text is not a bill number."""
    match = _BILL_QUERY.match(text)
    return f"{match[1].upper()}{int(match[2])}" if match else None


class CursorError(ValueError):
    """The after= cursor is malformed or belongs to another sort."""


def encode_cursor(sort: Sort, row: dict) -> str:
    values = [row[k].isoformat() if isinstance(row[k], date) else row[k] for k in SORT_KEYS[sort]]
    raw = json.dumps([sort.value, *values], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(sort: Sort, cursor: str) -> tuple:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        name, *values = json.loads(raw)
        keys = SORT_KEYS[sort]
        if name != sort.value or len(values) != len(keys):
            raise CursorError("cursor does not match the sort")
        return tuple(_KEY_TYPES[k](v) for k, v in zip(keys, values, strict=True))
    except CursorError:
        raise
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise CursorError("malformed cursor") from exc


class DashboardFilters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    q: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    status: StatusFilter | None = None
    committee: Annotated[str, StringConstraints(pattern=COMMITTEE_OR_CHAMBER)] | None = None
    sort: Sort = Sort.SEVERITY
    after: Annotated[str, Field(max_length=400)] | None = None

    @classmethod
    def from_args(cls, args: dict[str, str]) -> Self:
        """Blank values mean unset, as an HTML form submits them."""
        return cls.model_validate({k: v for k, v in args.items() if v != ""})

    @model_validator(mode="after")
    def _cursor_matches_sort(self) -> Self:
        if self.after is not None:
            decode_cursor(self.sort, self.after)
        return self

    @property
    def bill_prefix(self) -> str | None:
        """'h.10' -> 'H10' when q looks like a bill number, else None."""
        return normalize_bill_id(self.q)

    @property
    def chamber(self) -> str | None:
        """'H', 'S', or 'J' when the filter selects a whole chamber."""
        return self.committee if self.committee in CHAMBERS else None

    @property
    def committee_id(self) -> str | None:
        """The single committee selected, if any."""
        return None if self.chamber else self.committee

    @property
    def keyset(self) -> tuple | None:
        return None if self.after is None else decode_cursor(self.sort, self.after)

    def query_string(self, **changes: object) -> str:
        """Canonical query string for these filters, with changes; defaults are omitted."""
        values = self.model_dump() | changes
        if "after" not in changes:
            values["after"] = None
        params = {
            k: str(v)
            for k, v in values.items()
            if v not in (None, "") and not (k == "sort" and v == Sort.SEVERITY)
        }
        order = ("q", "status", "committee", "sort", "after")
        return urlencode([(k, params[k]) for k in order if k in params])
