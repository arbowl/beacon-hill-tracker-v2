"""Signed unsubscribe links. They do not expire, so an old email's link keeps working."""

from typing import Annotated

from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError

from tracker.enums import EmailKind

_SALT = "email-unsubscribe"


class UnsubscribeClaim(BaseModel):
    """The user a link belongs to and the email kinds the email it came in was sent for."""

    model_config = ConfigDict(frozen=True)

    user_id: PositiveInt
    kinds: Annotated[frozenset[EmailKind], Field(min_length=1)]


def sign(secret_key: str, user_id: int, kinds: frozenset[EmailKind]) -> str:
    claim = UnsubscribeClaim(user_id=user_id, kinds=kinds)
    return URLSafeSerializer(secret_key, salt=_SALT).dumps(
        {"user_id": claim.user_id, "kinds": sorted(claim.kinds)}
    )


def read(secret_key: str, token: str) -> UnsubscribeClaim | None:
    """The claim in a genuine link, or None."""
    try:
        payload = URLSafeSerializer(secret_key, salt=_SALT).loads(token)
        return UnsubscribeClaim.model_validate(payload)
    except (BadSignature, ValidationError):
        return None
