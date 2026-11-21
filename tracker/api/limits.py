"""Per-key request limit, counted in the database so it holds across workers."""

from tracker.db import Connection


def seconds_until_allowed(conn: Connection, key_id: int, per_minute: int) -> int:
    """Count this request in the current clock minute; 0 if allowed, else seconds to wait."""
    row = conn.execute(
        "INSERT INTO api_rate_windows (key_id, window_start, requests)"
        " VALUES (%s, date_trunc('minute', now()), 1)"
        " ON CONFLICT (key_id, window_start)"
        " DO UPDATE SET requests = api_rate_windows.requests + 1"
        " RETURNING requests,"
        "   ceil(extract(epoch FROM"
        "     date_trunc('minute', now()) + interval '1 minute' - now()))::int AS wait",
        (key_id,),
    ).fetchone()
    assert row is not None
    conn.execute(
        "DELETE FROM api_rate_windows WHERE key_id = %s"
        " AND window_start < date_trunc('minute', now())",
        (key_id,),
    )
    return 0 if row["requests"] <= per_minute else max(1, row["wait"])
