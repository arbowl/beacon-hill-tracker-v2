"""The public list of client releases."""

from flask import Blueprint, render_template

from tracker.changes import queries
from tracker.db import pool

bp = Blueprint("changes", __name__)


@bp.get("/changes")
def index() -> str:
    with pool().connection() as conn:
        releases = queries.releases(conn)
    return render_template("pages/changes.html", releases=releases)
