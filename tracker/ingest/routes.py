"""HTTP endpoints for client pushes."""

import hashlib
import logging
import time
import zlib
from collections.abc import Callable
from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, request
from pydantic import ValidationError

from beacon_contract import v1
from tracker.auth.keys import KeyOwner, authenticate
from tracker.config import Settings
from tracker.db import pool
from tracker.enums import KeyScope
from tracker.ingest import service
from tracker.ingest.service import IngestError
from tracker.publish.lifecycle import prune

bp = Blueprint("ingest", __name__, url_prefix="/api/ingest/v1")
log = logging.getLogger(__name__)
_GZIP_WINDOW = 16 + zlib.MAX_WBITS


def _error(status: int, code: str, message: str, details: tuple = ()) -> Response:
    body = v1.ErrorBody(error=v1.ErrorInfo(code=code, message=message, details=details))
    return Response(body.model_dump_json(), status=status, mimetype="application/json")


def _ok(state: v1.RunState) -> Response:
    return Response(state.model_dump_json(), mimetype="application/json")


def _settings() -> Settings:
    return cast(Settings, current_app.config["TRACKER_SETTINGS"])


@bp.before_request
def _authenticate() -> Response | None:
    header = request.headers.get("Authorization", "")
    scheme, _, secret = header.partition(" ")
    owner: KeyOwner | None = None
    if scheme == "Bearer" and secret:
        with pool().connection() as conn:
            owner = authenticate(conn, secret, KeyScope.INGEST)
    if owner is None:
        return _error(401, "unauthorized", "An ingest key is required")
    g.ingest_key = owner
    return None


@bp.errorhandler(IngestError)
def _ingest_error(exc: IngestError) -> Response:
    return _error(exc.http_status, exc.code, exc.message)


@bp.errorhandler(ValidationError)
def _validation_error(exc: ValidationError) -> Response:
    details = tuple(
        v1.ErrorDetail(loc=tuple(e["loc"]), msg=e["msg"])
        for e in exc.errors(include_url=False, include_input=False)
    )
    return _error(422, "invalid", f"{exc.error_count()} validation error(s)", details)


@bp.errorhandler(413)
def _too_large(_exc: Exception) -> Response:
    return _error(413, "too_large", "Request body is too large")


class _BodyError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _body() -> bytes:
    """The request body, gunzipped if sent with Content-Encoding: gzip, within size limits."""
    settings = _settings()
    if (request.content_length or 0) > settings.ingest_max_compressed_bytes:
        raise _BodyError(413, "too_large", "Request body is too large")
    raw = request.get_data(cache=False)
    if request.headers.get("Content-Encoding", "identity") == "identity":
        return raw
    if request.headers["Content-Encoding"] != "gzip":
        raise _BodyError(415, "unsupported_encoding", "Send identity or gzip")
    inflater = zlib.decompressobj(_GZIP_WINDOW)
    try:
        body = inflater.decompress(raw, settings.ingest_max_json_bytes)
    except zlib.error as exc:
        raise _BodyError(400, "bad_gzip", f"Body is not valid gzip: {exc}") from exc
    if inflater.unconsumed_tail:
        raise _BodyError(413, "too_large", "Uncompressed body is too large")
    return body


@bp.errorhandler(_BodyError)
def _body_error(exc: _BodyError) -> Response:
    return _error(exc.status, exc.code, exc.message)


def _timed(name: str, push_id: UUID, committee_id: str | None, fn: Callable[[], v1.RunState]):
    started = time.monotonic()
    state = fn()
    log.info(
        "ingest %s push=%s committee=%s status=%s ms=%d",
        name,
        push_id,
        committee_id or "-",
        state.status,
        (time.monotonic() - started) * 1000,
    )
    return state


@bp.post("/runs")
def open_run() -> Response:
    manifest = v1.RunManifest.model_validate_json(_body())
    with pool().connection() as conn:
        state = _timed(
            "open",
            manifest.push_id,
            None,
            lambda: service.open_run(conn, manifest, g.ingest_key.key_id),
        )
    return _ok(state)


@bp.get("/runs/<uuid:push_id>")
def get_run(push_id: UUID) -> Response:
    with pool().connection() as conn:
        state = service.run_state(conn, push_id)
    if state is None:
        return _error(404, service.IngestErrorCode.NOT_FOUND, f"No push {push_id}")
    return _ok(state)


@bp.put("/runs/<uuid:push_id>/committees/<committee_id>")
def upload(push_id: UUID, committee_id: str) -> Response:
    body = _body()
    bundle = v1.CommitteeBundle.model_validate_json(body)
    sha256 = hashlib.sha256(body).hexdigest()
    with pool().connection() as conn:
        state = _timed(
            "upload",
            push_id,
            committee_id,
            lambda: service.upload(conn, push_id, committee_id, bundle, sha256),
        )
    return _ok(state)


@bp.post("/runs/<uuid:push_id>/publish")
def publish(push_id: UUID) -> Response:
    with pool().connection() as conn:
        state = _timed("publish", push_id, None, lambda: service.publish(conn, push_id))
        settings = _settings()
        prune(conn, settings.retain_detail_publications, settings.abandon_open_runs_after_hours)
    return _ok(state)


@bp.put("/changelog")
def changelog() -> Response:
    body = v1.Changelog.model_validate_json(_body())
    with pool().connection() as conn:
        state = service.record_changelog(conn, body, g.ingest_key.key_id)
    log.info("ingest changelog code_version=%s updated=%s", body.code_version, state.updated)
    return Response(state.model_dump_json(), mimetype="application/json")


@bp.delete("/runs/<uuid:push_id>")
def abandon(push_id: UUID) -> Response:
    with pool().connection() as conn:
        state = _timed("abandon", push_id, None, lambda: service.abandon(conn, push_id))
    return _ok(state)
