"""The Watch control shown on bill pages and the committee view."""

from dataclasses import dataclass

from tracker.auth.current import current_user, here
from tracker.db import pool
from tracker.enums import WatchKind
from tracker.watch import queries
from tracker.watch.queries import WatchTarget

_BUTTON_TEXT = {WatchKind.BILL: "Watch this bill", WatchKind.COMMITTEE: "Watch this committee"}


@dataclass(frozen=True)
class WatchButton:
    target: WatchTarget
    watching: bool
    next_path: str

    @property
    def text(self) -> str:
        return _BUTTON_TEXT[self.target.kind]


def watch_button(kind: WatchKind, session: int, target_id: str) -> WatchButton:
    """The Watch control for a target, as the current visitor should see it."""
    target = WatchTarget(kind=kind, session=session, target_id=target_id)
    user = current_user()
    watching = False
    if user is not None:
        with pool().connection() as conn:
            watching = queries.is_watching(conn, user.user_id, target)
    return WatchButton(target, watching, here())
