"""Lenient publication-date parsing shared by the providers.

Providers report ``published`` in wildly different shapes (ISO-8601, RFC-1123,
bare dates, "2 days ago", or nothing). We normalize the ones we can to a
timezone-aware UTC :class:`datetime` and return ``None`` otherwise -- a missing
or unparseable date becomes a SQL NULL, never an error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def parse_published(value: object) -> datetime | None:
    """Best-effort parse of a provider's publication timestamp to UTC.

    Accepts ISO-8601 (with or without ``Z``), RFC-1123 (HTTP-date), and bare
    ``YYYY-MM-DD``. Returns ``None`` for empty, ``None``, or unrecognizable
    input so the ``published`` column reads NULL rather than failing.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_utc(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    # ISO-8601, tolerating a trailing 'Z'.
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return _to_utc(datetime.fromisoformat(iso))
    except ValueError:
        pass

    # RFC-1123 / HTTP-date (e.g. "Mon, 02 Jan 2006 15:04:05 GMT").
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None:
        return _to_utc(parsed)

    # Bare date.
    try:
        return _to_utc(datetime.strptime(text[:10], "%Y-%m-%d"))
    except ValueError:
        return None


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
