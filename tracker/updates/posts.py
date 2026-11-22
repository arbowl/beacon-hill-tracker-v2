"""Project updates: Markdown files with TOML front matter, loaded and rendered once at startup."""

import re
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self, cast

from flask import Flask, current_app
from markdown_it import MarkdownIt
from markdown_it.token import Token
from markupsafe import Markup
from pydantic import (
    BaseModel,
    ConfigDict,
    HttpUrl,
    StringConstraints,
    ValidationError,
    model_validator,
)

CONTENT_DIR = Path(__file__).resolve().parent / "content"
NOTICE_DAYS = 14
_CONFIG_KEY = "TRACKER_UPDATES"
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONT_MATTER = re.compile(r"\A\+\+\+\n(.*?)\n\+\+\+\n(.*)\Z", re.S)
_markdown = MarkdownIt("commonmark", {"html": False}).enable("table")

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class UpdateCategory(StrEnum):
    ANNOUNCEMENT = "announcement"
    IN_THE_NEWS = "in_the_news"
    ANALYSIS = "analysis"


CATEGORY_TEXT = {
    UpdateCategory.ANNOUNCEMENT: "Announcement",
    UpdateCategory.IN_THE_NEWS: "In the News",
    UpdateCategory.ANALYSIS: "Analysis",
}


class UpdateMeta(BaseModel):
    """Front matter. In the News posts name the outlet and link to the coverage; others do not."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: Text
    published: date
    category: UpdateCategory
    summary: Text
    updated: date | None = None
    source: Text | None = None
    url: HttpUrl | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        news = self.category is UpdateCategory.IN_THE_NEWS
        if news and (self.source is None or self.url is None):
            raise ValueError("in_the_news posts need source and url")
        if not news and (self.source is not None or self.url is not None):
            raise ValueError("only in_the_news posts take source and url")
        if self.updated is not None and self.updated < self.published:
            raise ValueError("updated is before published")
        return self


@dataclass(frozen=True)
class Update:
    slug: str
    meta: UpdateMeta
    html: Markup
    search_text: str

    def matches(self, terms: Iterable[str]) -> bool:
        return all(term in self.search_text for term in terms)


class UpdateError(ValueError):
    """An update file is malformed; the message names the file."""


def _plain_text(tokens: Sequence[Token]) -> str:
    parts = []
    for token in tokens:
        if token.type in ("fence", "code_block"):
            parts.append(token.content)
        parts.extend(c.content for c in token.children or () if c.type in ("text", "code_inline"))
    return " ".join(parts)


def parse(slug: str, text: str) -> Update:
    match = _FRONT_MATTER.match(text.replace("\r\n", "\n"))
    if match is None:
        raise UpdateError(f"{slug}: expected front matter between +++ lines at the top")
    try:
        meta = UpdateMeta.model_validate(tomllib.loads(match[1]))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise UpdateError(f"{slug}: {exc}") from exc
    tokens = _markdown.parse(match[2])
    body = _plain_text(tokens)
    if not body.strip():
        raise UpdateError(f"{slug}: the body is empty")
    searchable = " ".join(filter(None, (meta.title, meta.summary, meta.source, body)))
    return Update(
        slug=slug,
        meta=meta,
        html=Markup(_markdown.renderer.render(tokens, _markdown.options, {})),
        search_text=searchable.casefold(),
    )


def load(directory: Path) -> tuple[Update, ...]:
    """Every *.md file in directory, newest first. The file name (without .md) is the slug."""
    updates = []
    for path in directory.glob("*.md"):
        if not _SLUG.match(path.stem):
            raise UpdateError(f"{path.name}: file names must be lowercase words joined by hyphens")
        updates.append(parse(path.stem, path.read_text(encoding="utf-8")))
    return tuple(sorted(updates, key=lambda u: (u.meta.published, u.slug), reverse=True))


def install(app: Flask, updates: tuple[Update, ...]) -> None:
    app.config[_CONFIG_KEY] = updates


def published() -> tuple[Update, ...]:
    return cast(tuple[Update, ...], current_app.config[_CONFIG_KEY])


def latest() -> Update | None:
    updates = published()
    return updates[0] if updates else None


def recent(today: date) -> Update | None:
    """The newest update if it was published within the last NOTICE_DAYS days."""
    newest = latest()
    if newest is None or newest.meta.published <= today - timedelta(days=NOTICE_DAYS):
        return None
    return newest
