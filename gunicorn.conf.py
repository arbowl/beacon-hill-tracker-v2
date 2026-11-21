"""Gunicorn settings, loaded automatically from the working directory."""

import sys
import threading
import traceback


def _log_threads(log, label: str) -> None:
    names = {t.ident: t.name for t in threading.enumerate()}
    loaded = [m for m in ("tracker", "psycopg_pool", "encodings.idna") if m in sys.modules]
    log.critical("%s: modules loaded %s", label, loaded)
    for ident, frame in sys._current_frames().items():
        stack = "".join(traceback.format_stack(frame))
        log.critical("%s: thread %s (%s):\n%s", label, ident, names.get(ident, "?"), stack)


def pre_fork(server, worker) -> None:
    """Log the master's threads just before each worker is forked."""
    _log_threads(server.log, "master pre_fork")


def post_worker_init(worker) -> None:
    """Log the worker's threads once the app is loaded."""
    _log_threads(worker.log, "worker post_init")


def worker_abort(worker) -> None:
    """Log every thread's stack when a worker is killed for timing out."""
    _log_threads(worker.log, "worker abort")
