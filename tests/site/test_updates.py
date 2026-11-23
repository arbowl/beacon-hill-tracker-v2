"""Updates load from Markdown files, render safely, and can be searched, filtered, and sorted."""

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from tests.conftest import Pusher, bundle_dict
from tracker.changes.queries import ShownRelease
from tracker.dashboard.routes import _hero_notices
from tracker.display import LEGISLATURE_TZ
from tracker.updates import posts

TODAY = datetime.now(LEGISLATURE_TZ).date()


def _post(
    slug: str, title: str, category: str = "announcement", published: date = TODAY, **extra: str
) -> tuple[str, str]:
    lines = [f'title = "{title}"', f"published = {published.isoformat()}"]
    lines += [f'category = "{category}"', f'summary = "Summary of {title}."']
    lines += [f'{k} = "{v}"' for k, v in extra.items()]
    return slug, "+++\n" + "\n".join(lines) + f"\n+++\n\nBody of {title} with *emphasis*.\n"


def _install(app: Flask, tmp_path: Path, *files: tuple[str, str]) -> None:
    for slug, text in files:
        (tmp_path / f"{slug}.md").write_text(text, encoding="utf-8")
    posts.install(app, posts.load(tmp_path))


@pytest.fixture
def three(app: Flask, tmp_path: Path) -> None:
    _install(
        app,
        tmp_path,
        _post("launch", "Launch notes", published=TODAY - timedelta(days=40)),
        _post(
            "globe-story",
            "Coverage of deadlines",
            "in_the_news",
            TODAY - timedelta(days=20),
            source="The Daily Example",
            url="https://example.org/story",
        ),
        _post("summary-gaps", "Where summaries go missing", "analysis", TODAY - timedelta(days=2)),
    )


def _titles(html: str) -> list[str]:
    return re.findall(r'<h2><a href="/updates/[^"]+">([^<]+)</a></h2>', html)


def test_committed_updates_load():
    for update in posts.load(posts.CONTENT_DIR):
        assert update.html and update.meta.summary


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("No front matter.\n", "front matter"),
        (_post("x", "X", "rumor")[1], "category"),
        (_post("x", "X", "in_the_news")[1], "source and url"),
        (_post("x", "X", source="Paper", url="https://example.org")[1], "only in_the_news"),
        (_post("x", "X", typo="y")[1], "typo"),
        ('+++\ntitle = "X"\n+++\n\nBody\n', "published"),
        (_post("x", "X")[1].split("\n+++\n")[0] + "\n+++\n\n   \n", "body is empty"),
    ],
)
def test_malformed_updates_are_rejected(text: str, error: str):
    with pytest.raises(posts.UpdateError, match=error):
        posts.parse("x", text)


def test_bad_file_name_is_rejected(tmp_path: Path):
    (tmp_path / "Bad Name.md").write_text(_post("x", "X")[1], encoding="utf-8")
    with pytest.raises(posts.UpdateError, match="file names"):
        posts.load(tmp_path)


def test_raw_html_is_escaped_and_crlf_is_accepted():
    text = _post("x", "X")[1].replace("\n", "\r\n") + '\r\n<div style="color:red">hi</div>\r\n'
    update = posts.parse("x", text)
    assert "<em>emphasis</em>" in update.html
    assert "<div" not in update.html and "&lt;div" in update.html


def test_list_is_newest_first_and_sortable(client: FlaskClient, three: None):
    html = client.get("/updates").get_data(as_text=True)
    assert _titles(html) == ["Where summaries go missing", "Coverage of deadlines", "Launch notes"]
    assert "Coverage in The Daily Example" in html
    assert re.search(r"style=\"", html) is None
    oldest = client.get("/updates?sort=oldest").get_data(as_text=True)
    assert _titles(oldest)[0] == "Launch notes"


def test_search_and_category(client: FlaskClient, three: None):
    assert _titles(client.get("/updates?q=EMPHASIS+launch").get_data(as_text=True)) == [
        "Launch notes"
    ]
    assert _titles(client.get("/updates?q=daily+example").get_data(as_text=True)) == [
        "Coverage of deadlines"
    ]
    html = client.get("/updates?category=analysis&q=&sort=").get_data(as_text=True)
    assert _titles(html) == ["Where summaries go missing"]
    assert "No updates match" in client.get("/updates?q=zzz").get_data(as_text=True)
    assert client.get("/updates?category=rumor").status_code == 400


def test_htmx_returns_the_list_and_pushes_the_url(client: FlaskClient, three: None):
    response = client.get("/updates?category=in_the_news&sort=newest", headers={"HX-Request": "1"})
    html = response.get_data(as_text=True)
    assert html.lstrip().startswith('<section id="updates-list"') and "<form" not in html
    assert response.headers["HX-Push-Url"] == "/updates?category=in_the_news"


def test_update_page(client: FlaskClient, three: None):
    html = client.get("/updates/globe-story").get_data(as_text=True)
    assert "<h1>Coverage of deadlines</h1>" in html
    assert 'href="https://example.org/story"' in html
    assert client.get("/updates/nope").status_code == 404


def test_no_updates(client: FlaskClient, app: Flask, tmp_path: Path):
    _install(app, tmp_path)
    html = client.get("/updates").get_data(as_text=True)
    assert "No updates have been posted yet" in html and "<form" not in html
    assert "The latest is" not in client.get("/about").get_data(as_text=True)


def test_latest_on_about_and_press(client: FlaskClient, three: None):
    for url in ("/about", "/press"):
        assert 'The latest is <a href="/updates/summary-gaps">' in client.get(url).get_data(
            as_text=True
        )
    assert 'href="/updates">Updates</a>' in client.get("/about").get_data(as_text=True)


def test_dashboard_notice_only_while_recent(
    client: FlaskClient, app: Flask, tmp_path: Path, pusher: Pusher
):
    pusher.push({"J19": bundle_dict("J19")})
    _install(app, tmp_path, _post("fresh", "Fresh post", published=TODAY - timedelta(days=1)))
    first = client.get("/")
    assert "Site update</strong>" in first.get_data(as_text=True)
    assert 'href="/updates/fresh">Fresh post</a>' in first.get_data(as_text=True)
    stale = TODAY - timedelta(days=posts.NOTICE_DAYS)
    (tmp_path / "fresh.md").unlink()
    _install(app, tmp_path, _post("stale", "Stale post", published=stale))
    second = client.get("/", headers={"If-None-Match": first.headers["ETag"]})
    assert second.status_code == 200 and "Site update" not in second.get_data(as_text=True)


def test_hero_notices_are_oldest_first(app: Flask):
    update = posts.parse(*_post("post", "Post", published=TODAY))
    with app.test_request_context():
        for gap, labels in (
            (1, ["Methods update", "Site update"]),
            (-1, ["Site update", "Methods update"]),
        ):
            release = ShownRelease("2.0.0", "h", TODAY - timedelta(days=gap), (), None)
            assert [n.label for n in _hero_notices(release, update)] == labels
