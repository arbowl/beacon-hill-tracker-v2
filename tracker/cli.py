"""Host-only commands: `flask db|users|keys|runs|alerts|emails ...`. Roles are granted only here."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import click
from flask import Flask, current_app
from flask.cli import AppGroup

from tracker.alerts import events
from tracker.alerts.digest import DigestBusy, send_digests
from tracker.auth import accounts
from tracker.auth.keys import KeyNotAllowed, create_key
from tracker.config import Settings
from tracker.db import Connection, connect, migrate, pool
from tracker.display import LEGISLATURE_TZ
from tracker.enums import EmailKind, KeyScope, UserRole
from tracker.mail.mailings import Announcement, recipients, send_announcement, send_weekly
from tracker.publish import dumps
from tracker.publish.lifecycle import RollbackError, prune, rollback

db_cli = AppGroup("db", help="Schema migrations.")
users_cli = AppGroup("users", help="Accounts and roles.")
keys_cli = AppGroup("keys", help="API keys.")
runs_cli = AppGroup("runs", help="Published data.")
dumps_cli = AppGroup("dumps", help="Data downloads.")
alerts_cli = AppGroup("alerts", help="Watch alerts.")
emails_cli = AppGroup("emails", help="Site-wide mailings.")


def _settings() -> Settings:
    return cast(Settings, current_app.config["TRACKER_SETTINGS"])


@contextmanager
def _mailer() -> Iterator[Connection]:
    """An autocommit connection inside a request context, for jobs that send email."""
    settings = _settings()
    if not settings.links_configured:
        raise click.ClickException("Set SMTP_HOST, MAIL_FROM and SITE_URL to send email")
    with (
        current_app.test_request_context(),
        connect(settings.database_url, autocommit=True) as conn,
    ):
        yield conn


def _today() -> datetime:
    return datetime.now(LEGISLATURE_TZ)


def _user_id(email: str) -> int:
    with pool().connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
    if row is None:
        raise click.ClickException(f"No user {email}")
    return row["id"]


@db_cli.command("migrate")
def db_migrate() -> None:
    """Apply pending migrations."""
    with connect(_settings().database_url, autocommit=True) as conn:
        applied = migrate(conn)
    click.echo("\n".join(f"applied {n}" for n in applied) or "up to date")


@users_cli.command("create")
@click.argument("email")
@click.option("--role", type=click.Choice([r.value for r in UserRole]), default=UserRole.USER.value)
def users_create(email: str, role: str) -> None:
    """Create an account with every email turned off."""
    with pool().connection() as conn:
        try:
            accounts.create(conn, email, UserRole(role), frozenset())
        except accounts.AccountExists as exc:
            raise click.ClickException(f"{email} already exists") from exc
    click.echo(f"created {email} ({role})")


@users_cli.command("set-role")
@click.argument("email")
@click.argument("role", type=click.Choice([r.value for r in UserRole]))
def users_set_role(email: str, role: str) -> None:
    """Change an account's role."""
    with pool().connection() as conn:
        changed = conn.execute(
            "UPDATE users SET role = %s WHERE email = %s", (role, email)
        ).rowcount
    if not changed:
        raise click.ClickException(f"No user {email}")
    click.echo(f"{email} is now {role}")


@users_cli.command("list")
@click.option("--role", type=click.Choice([r.value for r in UserRole]))
def users_list(role: str | None) -> None:
    """List accounts."""
    with pool().connection() as conn:
        rows = conn.execute(
            "SELECT email, role, disabled_at FROM users"
            " WHERE %(role)s::user_role IS NULL OR role = %(role)s ORDER BY email",
            {"role": role},
        ).fetchall()
    for r in rows:
        click.echo(f"{r['email']}\t{r['role']}\t{'disabled' if r['disabled_at'] else 'enabled'}")


@users_cli.command("disable")
@click.argument("email")
def users_disable(email: str) -> None:
    """Disable an account; its keys stop working."""
    with pool().connection() as conn:
        changed = conn.execute(
            "UPDATE users SET disabled_at = now() WHERE email = %s AND disabled_at IS NULL",
            (email,),
        ).rowcount
    if not changed:
        raise click.ClickException(f"No enabled user {email}")
    click.echo(f"disabled {email}")


@keys_cli.command("create")
@click.argument("email")
@click.option("--scope", type=click.Choice([s.value for s in KeyScope]), required=True)
@click.option("--name", required=True, help="What the key is for")
def keys_create(email: str, scope: str, name: str) -> None:
    """Create a key and print it once."""
    user_id = _user_id(email)
    with pool().connection() as conn:
        try:
            key = create_key(conn, user_id, KeyScope(scope), name)
        except KeyNotAllowed as exc:
            raise click.ClickException(str(exc)) from exc
    click.echo(key.secret)
    click.echo("Store this key now; it is not shown again.", err=True)


@keys_cli.command("revoke")
@click.argument("prefix")
def keys_revoke(prefix: str) -> None:
    """Revoke the live key with this display prefix."""
    with pool().connection() as conn:
        changed = conn.execute(
            "UPDATE api_keys SET revoked_at = now() WHERE prefix = %s AND revoked_at IS NULL",
            (prefix,),
        ).rowcount
    if changed != 1:
        raise click.ClickException(f"{changed} live keys match {prefix}")
    click.echo(f"revoked {prefix}")


@runs_cli.command("list")
@click.option("--limit", default=20, show_default=True)
def runs_list(limit: int) -> None:
    """Recent pushes, newest first."""
    with pool().connection() as conn:
        rows = conn.execute(
            "SELECT run_id, status, session, scope, opened_at, published_at FROM runs"
            " ORDER BY opened_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
    for r in rows:
        published = r["published_at"].isoformat() if r["published_at"] else "-"
        click.echo(
            f"{r['run_id']}\t{r['status']}\t{r['session']}\t{','.join(r['scope'])}\t{published}"
        )


@runs_cli.command("rollback")
@click.argument("committee_id")
@click.option("--session", type=int, required=True)
def runs_rollback(committee_id: str, session: int) -> None:
    """Withdraw a committee's latest publication and restore the previous one."""
    with pool().connection() as conn:
        try:
            result = rollback(conn, session, committee_id)
        except RollbackError as exc:
            raise click.ClickException(str(exc)) from exc
    click.echo(f"{committee_id}: withdrew {result.rolled_back_run}, restored {result.restored_run}")
    _rebuild_dumps(session)


@runs_cli.command("prune")
def runs_prune() -> None:
    """Delete detail no longer retained, and abandon stale open pushes."""
    settings = _settings()
    with pool().connection() as conn:
        result = prune(
            conn, settings.retain_detail_publications, settings.abandon_open_runs_after_hours
        )
    click.echo(
        f"pruned {result.committees_pruned} committee uploads, {result.runs_pruned} runs;"
        f" abandoned {result.runs_abandoned}"
    )


@alerts_cli.command("scan")
def alerts_scan() -> None:
    """Record reminders for live deadlines in the next 7 days (daily)."""
    with connect(_settings().database_url, autocommit=True) as conn:
        count = events.scan_deadlines(conn, _today().date())
    click.echo(f"{count} new deadline reminders")


@alerts_cli.command("send")
def alerts_send() -> None:
    """Email each watcher a digest of what they have not been sent (hourly)."""
    with _mailer() as conn:
        try:
            result = send_digests(conn, _settings())
        except DigestBusy as exc:
            raise click.ClickException(str(exc)) from exc
    click.echo(
        f"sent {result.emails_sent} digests with {result.events_delivered} events;"
        f" {result.failures} failed"
    )
    if result.failures:
        raise SystemExit(1)


@emails_cli.command("weekly")
def emails_weekly() -> None:
    """Send this week's summary to everyone who asked for it (weekly)."""
    now = _today()
    with _mailer() as conn:
        result = send_weekly(conn, _settings(), now.date(), now.astimezone(UTC))
    click.echo(f"sent {result.sent}; {result.failures} failed")
    if result.failures:
        raise SystemExit(1)


@emails_cli.command("announce")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--yes", is_flag=True, help="Send without asking for confirmation")
def emails_announce(path: Path, yes: bool) -> None:
    """Send an announcement file (subject on the first line) to everyone who opted in.

    The file's name is its key: each account gets a given name once, so a rerun only reaches
    accounts it missed.
    """
    try:
        announcement = Announcement.parse(path.stem, path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    with _mailer() as conn:
        pending = len(recipients(conn, EmailKind.ANNOUNCEMENTS, announcement.key))
        click.echo(f"{announcement.subject!r} ({announcement.key}) to {pending} accounts")
        if not pending or not (yes or click.confirm("Send it?")):
            return
        result = send_announcement(conn, _settings(), announcement)
    click.echo(f"sent {result.sent}; {result.failures} failed")
    if result.failures:
        raise SystemExit(1)


def _rebuild_dumps(session: int) -> None:
    settings = _settings()
    with connect(settings.database_url, autocommit=True) as conn:
        built = dumps.refresh(
            conn, session, settings.retain_dumps_recent, settings.retain_dumps_monthly
        )
    for d in built:
        click.echo(
            f"{d.format}	{d.size} bytes	sha256 {d.sha256}	as of {d.as_of.isoformat()}"
        )


@dumps_cli.command("rebuild")
@click.option("--session", type=int, help="Defaults to the newest session with published data")
def dumps_rebuild(session: int | None) -> None:
    """Make the live data downloadable and apply retention (every 10 minutes; a no-op when
    the files are current)."""
    if session is None:
        with pool().connection() as conn:
            row = conn.execute("SELECT max(session) AS s FROM committee_current").fetchone()
        if row is None or row["s"] is None:
            raise click.ClickException("No data has been published")
        session = row["s"]
    _rebuild_dumps(session)


def register(app: Flask) -> None:
    for group in (db_cli, users_cli, keys_cli, runs_cli, dumps_cli, alerts_cli, emails_cli):
        app.cli.add_command(group)
