"""Gunicorn settings, loaded automatically from the working directory."""

import sys
import threading
import traceback

# The control server runs as a thread in the master; forking a worker while it holds an
# import lock leaves that lock held forever in the child.
control_socket_disable = True


def worker_abort(worker) -> None:
    """Log every thread's stack when a worker is killed for timing out."""
    names = {t.ident: t.name for t in threading.enumerate()}
    for ident, frame in sys._current_frames().items():
        stack = "".join(traceback.format_stack(frame))
        worker.log.critical("Thread %s (%s):\n%s", ident, names.get(ident, "?"), stack)
