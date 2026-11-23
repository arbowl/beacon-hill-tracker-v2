"""Beacon Hill Compliance Tracker site."""

import logging

from flask import Flask, Response
from pydantic import ValidationError

from tracker import cli, csrf, web
from tracker.api.routes import bp as api_bp
from tracker.auth.routes import bp as auth_bp
from tracker.bills.routes import bp as bills_bp
from tracker.changes.routes import bp as changes_bp
from tracker.config import Settings
from tracker.dashboard.routes import bp as dashboard_bp
from tracker.db import init_pool, pool
from tracker.embed.routes import bp as embed_bp
from tracker.ingest.routes import bp as ingest_bp
from tracker.mail.routes import bp as email_bp
from tracker.pages.routes import bp as pages_bp
from tracker.updates import posts
from tracker.updates.routes import bp as updates_bp
from tracker.watch.routes import bp as watch_bp


def create_app(settings: Settings | None = None) -> Flask:
    if settings is None:
        try:
            settings = Settings()  # type: ignore[call-arg]
        except ValidationError as exc:
            missing = ", ".join(str(e["loc"][0]).upper() for e in exc.errors())
            raise SystemExit(
                f"Missing or invalid settings: {missing}. Copy .env.example to .env in the"
                " directory you run from, or set them as environment variables."
            ) from exc
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=settings.secret_key,
        MAX_CONTENT_LENGTH=settings.ingest_max_compressed_bytes,
        SESSION_COOKIE_SECURE=settings.secure_cookies,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        TRACKER_SETTINGS=settings,
        TRACKER_BUILD=web.build_fingerprint(),
    )
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    init_pool(app, settings)
    posts.install(app, posts.load(posts.CONTENT_DIR))
    blueprints = (
        ingest_bp,
        api_bp,
        dashboard_bp,
        bills_bp,
        changes_bp,
        pages_bp,
        updates_bp,
        auth_bp,
        watch_bp,
        email_bp,
        embed_bp,
    )
    for blueprint in blueprints:
        app.register_blueprint(blueprint)
    web.register(app)
    app.add_template_global(csrf.token, "csrf_token")
    cli.register(app)

    @app.get("/healthz")
    def healthz() -> Response:
        with pool().connection() as conn:
            conn.execute("SELECT 1")
        return Response("ok", mimetype="text/plain")

    return app
