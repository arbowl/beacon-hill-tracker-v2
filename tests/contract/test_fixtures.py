"""Bundles produced by the client from real J19 and J45 data validate."""

import gzip
from pathlib import Path

import pytest

from beacon_contract.v1 import CommitteeBundle

BUNDLES = Path(__file__).resolve().parents[1] / "fixtures" / "bundles"


@pytest.mark.parametrize("committee_id", ["J19", "J45"])
def test_client_bundle_validates(committee_id: str):
    raw = gzip.decompress((BUNDLES / f"{committee_id}.bundle.json.gz").read_bytes())
    bundle = CommitteeBundle.model_validate_json(raw)
    assert bundle.committee.committee_id == committee_id
    assert bundle.tenures
