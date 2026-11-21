"""Every boundary rule of the v1 changelog accepts good changelogs and rejects bad ones."""

import copy
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from beacon_contract.v1 import Changelog


def changelog() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "code_version": "0.2.0",
        "releases": [
            {
                "version": "0.2.0",
                "released_on": "2026-12-01",
                "sections": [
                    {"kind": "Changed", "items": ["Hearing notice counts calendar days."]},
                    {"kind": "Fixed", "items": ["Hearings moved to a new date are read."]},
                ],
            },
            {
                "version": "0.1.0",
                "released_on": "2026-11-19",
                "sections": [{"kind": "Added", "items": ["Checks four requirements."]}],
            },
        ],
    }


def test_valid_changelog_round_trips():
    model = Changelog.model_validate(changelog())
    assert Changelog.model_validate_json(model.model_dump_json()) == model
    assert model.releases[0].semver == (0, 2, 0)


def _set(path: Callable[[dict], dict], key: str, value: Any) -> Callable[[dict], None]:
    def mutate(c: dict) -> None:
        path(c)[key] = value

    return mutate


def _newest(c: dict) -> dict:
    return c["releases"][0]


def _repeat_release(c: dict) -> None:
    c["releases"].append(copy.deepcopy(c["releases"][1]))


def _oldest_first(c: dict) -> None:
    c["releases"].reverse()
    c["code_version"] = "0.1.0"


def _repeat_kind(c: dict) -> None:
    sections = _newest(c)["sections"]
    sections.append(copy.deepcopy(sections[0]))


REJECTIONS: dict[str, Callable[[dict], None]] = {
    "unknown field": _set(lambda c: c, "surprise", 1),
    "schema version": _set(lambda c: c, "schema_version", 2),
    "no releases": _set(lambda c: c, "releases", []),
    "code version not newest": _set(lambda c: c, "code_version", "0.1.0"),
    "code version form": _set(lambda c: c, "code_version", "0.2"),
    "version with v prefix": _set(_newest, "version", "v0.2.0"),
    "version with leading zero": _set(_newest, "version", "0.02.0"),
    "pre-release version": _set(_newest, "version", "0.2.0-rc.1"),
    "unreleased": _set(_newest, "version", "Unreleased"),
    "repeated version": _repeat_release,
    "oldest first": _oldest_first,
    "newer dated earlier": _set(_newest, "released_on", "2026-11-01"),
    "no sections": _set(_newest, "sections", []),
    "unknown kind": _set(lambda c: _newest(c)["sections"][0], "kind", "Improved"),
    "repeated kind": _repeat_kind,
    "empty section": _set(lambda c: _newest(c)["sections"][0], "items", []),
    "empty item": _set(lambda c: _newest(c)["sections"][0], "items", [""]),
    "overlong item": _set(lambda c: _newest(c)["sections"][0], "items", ["x" * 2001]),
}


@pytest.mark.parametrize("mutate", REJECTIONS.values(), ids=REJECTIONS.keys())
def test_rejects(mutate: Callable[[dict], None]):
    c = changelog()
    mutate(c)
    with pytest.raises(ValidationError):
        Changelog.model_validate(c)
