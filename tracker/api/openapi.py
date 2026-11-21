"""OpenAPI 3.1 document for the public API, generated from its response models."""

from dataclasses import dataclass
from functools import cache
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from beacon_contract import v1
from tracker.api import models
from tracker.dashboard.filters import Sort
from tracker.display import StatusFilter

_REF = "#/components/schemas/{model}"


@dataclass(frozen=True)
class Param:
    name: str
    where: str
    description: str
    schema: dict[str, Any]
    required: bool = False


@dataclass(frozen=True)
class Endpoint:
    path: str
    summary: str
    response: type[BaseModel]
    params: tuple[Param, ...] = ()


_COMMITTEE = {"type": "string", "pattern": v1.COMMITTEE_ID_PATTERN}
ENDPOINTS = (
    Endpoint(
        "/api/v1/stats",
        "Summary figures for the newest session, or one committee in it",
        models.Stats,
        (Param("committee", "query", "A committee id, such as J19", _COMMITTEE),),
    ),
    Endpoint("/api/v1/committees", "Committees in the newest session", models.CommitteeList),
    Endpoint(
        "/api/v1/committees/{committee_id}",
        "One committee and its summary figures",
        models.CommitteeDetail,
        (Param("committee_id", "path", "A committee id, such as J19", _COMMITTEE, True),),
    ),
    Endpoint(
        "/api/v1/segments",
        "Bill segments in the newest session, 50 per page, with the dashboard's filters",
        models.SegmentPage,
        (
            Param(
                "q",
                "query",
                "A bill number or words from a title",
                {"type": "string", "maxLength": 100},
            ),
            Param("status", "query", "Label to match", {"enum": [s.value for s in StatusFilter]}),
            Param("committee", "query", "A committee id", _COMMITTEE),
            Param(
                "sort", "query", "Order; it only sorts the list", {"enum": [s.value for s in Sort]}
            ),
            Param("after", "query", "next_cursor from the previous page", {"type": "string"}),
        ),
    ),
    Endpoint(
        "/api/v1/bills/{session}/{bill_id}",
        "A bill and each of its segments, with full results and cited rule text",
        models.BillDetail,
        (
            Param("session", "path", "A General Court, such as 194", {"type": "integer"}, True),
            Param("bill_id", "path", "A bill number, such as H10", {"type": "string"}, True),
        ),
    ),
)

_ERRORS = {
    "400": "Invalid parameters",
    "401": "Missing, revoked, or unknown read key",
    "404": "Not found, or no data published yet",
    "429": "Over this key's per-minute request limit; see Retry-After",
}


@cache
def document() -> dict[str, Any]:
    schemas = [(e.response, "serialization") for e in ENDPOINTS] + [(v1.ErrorBody, "serialization")]
    _, top = models_json_schema(schemas, ref_template=_REF)
    paths: dict[str, Any] = {}
    for e in ENDPOINTS:
        responses: dict[str, Any] = {
            "200": {
                "description": "OK",
                "content": {
                    "application/json": {"schema": {"$ref": _REF.format(model=e.response.__name__)}}
                },
            }
        }
        for code, text in _ERRORS.items():
            responses[code] = {
                "description": text,
                "content": {
                    "application/json": {"schema": {"$ref": _REF.format(model="ErrorBody")}}
                },
            }
        paths[e.path] = {
            "get": {
                "summary": e.summary,
                "parameters": [
                    {
                        "name": p.name,
                        "in": p.where,
                        "description": p.description,
                        "required": p.required,
                        "schema": p.schema,
                    }
                    for p in e.params
                ],
                "responses": responses,
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Beacon Hill Compliance Tracker API",
            "version": "1",
            "description": (
                "Read-only access to the Tracker's published data. Every request needs a read"
                " key, created on the watchlist page. Figures match the dashboard for the same"
                " filters. A detection reflects what was posted online, which may differ from"
                " what a committee did; see /methodology."
            ),
        },
        "security": [{"readKey": []}],
        "paths": paths,
        "components": {
            "schemas": top.get("$defs", {}),
            "securitySchemes": {"readKey": {"type": "http", "scheme": "bearer"}},
        },
    }
