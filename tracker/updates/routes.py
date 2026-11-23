"""The updates list, with search, category filter, and sort, and one page per update."""

from enum import StrEnum
from typing import Annotated, Self
from urllib.parse import urlencode

from flask import Blueprint, Response, abort, make_response, render_template, request
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from tracker.updates.posts import CATEGORY_TEXT, Update, UpdateCategory, published

bp = Blueprint("updates", __name__)


class Sort(StrEnum):
    NEWEST = "newest"
    OLDEST = "oldest"


SORT_TEXT = {Sort.NEWEST: "Newest first", Sort.OLDEST: "Oldest first"}


class UpdateFilters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    q: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    category: UpdateCategory | None = None
    sort: Sort = Sort.NEWEST

    @classmethod
    def from_args(cls, args: dict[str, str]) -> Self:
        """Blank values mean unset, as an HTML form submits them."""
        return cls.model_validate({k: v for k, v in args.items() if v != ""})

    def query_string(self) -> str:
        """Canonical query string; defaults are omitted."""
        params = [("q", self.q), ("category", self.category or ""), ("sort", self.sort)]
        return urlencode([(k, v) for k, v in params if v and v != Sort.NEWEST])

    def apply(self, updates: tuple[Update, ...]) -> list[Update]:
        terms = self.q.casefold().split()
        found = [
            u
            for u in updates
            if (self.category is None or u.meta.category is self.category) and u.matches(terms)
        ]
        return found[::-1] if self.sort is Sort.OLDEST else found


@bp.get("/updates")
def index() -> Response:
    try:
        filters = UpdateFilters.from_args(request.args.to_dict())
    except ValidationError:
        abort(400, description="Invalid filter value")
    updates = published()
    context = dict(
        filters=filters,
        found=filters.apply(updates),
        total=len(updates),
        categories=list(CATEGORY_TEXT.items()),
        sort_options=list(SORT_TEXT.items()),
    )
    if not request.headers.get("HX-Request"):
        response = make_response(render_template("pages/updates.html", **context))
    else:
        response = make_response(render_template("partials/_updates_list.html", **context))
        query = filters.query_string()
        response.headers["HX-Push-Url"] = f"/updates?{query}" if query else "/updates"
    response.headers["Vary"] = "HX-Request"
    return response


@bp.get("/updates/<slug>")
def show(slug: str) -> str:
    update = next((u for u in published() if u.slug == slug), None)
    if update is None:
        abort(404)
    return render_template("pages/update.html", update=update)
