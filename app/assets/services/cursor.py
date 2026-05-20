"""Opaque keyset-pagination cursor for /api/assets.

Wire format mirrors the cloud Go implementation in
`common/pagination/cursor.go` so both runtimes produce byte-identical
cursors for the same `(sort_field, value, id)` triple and the frontend
sees one contract.

Payload JSON uses short keys to keep the encoded length small:

    {"s": <sort_field>, "v": <value>, "id": <id>}

Encoding is base64url with no padding. Time values are serialized as Unix
microseconds (UTC) — microsecond precision matches PostgreSQL's
`timestamp` type, so a cursor minted from a stored timestamp compares
back exactly without rounding rows in the same millisecond bucket.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional


class InvalidCursorError(ValueError):
    """Raised on a malformed, oversized, or unsupported-sort-field cursor.

    Map to a 400 response with code ``INVALID_CURSOR`` at the handler.
    """


# Wire-format length caps. Cursors are user-controlled, so caps protect the
# decode path from oversized allocations and downstream SQL predicates from
# unbounded strings. Same numbers as cloud/common/pagination/cursor.go.
MAX_ENCODED_CURSOR_LENGTH = 1024
MAX_CURSOR_VALUE_LENGTH = 256
MAX_CURSOR_ID_LENGTH = 128


@dataclass(frozen=True)
class CursorPayload:
    sort_field: str
    value: str
    id: str


def encode_cursor(sort_field: str, value: str, id: str) -> str:
    """Encode a cursor payload as a base64url (no-padding) string."""
    payload = {"s": sort_field, "v": value, "id": id}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def encode_cursor_from_time(sort_field: str, t: datetime, id: str) -> str:
    """Encode a time-typed cursor at Unix microsecond precision.

    Accepts an aware datetime (any timezone) and normalizes to UTC. Naive
    datetimes are rejected so callers can't accidentally encode the local
    wall-clock value of a UTC-stored timestamp.
    """
    if t.tzinfo is None:
        raise ValueError("encode_cursor_from_time requires an aware datetime")
    micros = _datetime_to_unix_micros(t.astimezone(timezone.utc))
    return encode_cursor(sort_field, str(micros), id)


def decode_cursor(cursor: str, allowed_sort_fields: Iterable[str]) -> CursorPayload:
    """Parse an opaque cursor.

    ``allowed_sort_fields`` is the endpoint's accepted sort-field list — a
    cursor carrying a field outside this set is rejected so a cursor minted
    for one column can't be replayed against another (e.g. a ``created_at``
    timestamp string compared against a ``name`` column).

    Passing no allowed fields rejects every cursor.
    """
    if len(cursor) > MAX_ENCODED_CURSOR_LENGTH:
        raise InvalidCursorError("cursor exceeds maximum length")

    try:
        # urlsafe_b64decode requires correct padding; we strip on encode, so
        # restore the trailing '=' pad here.
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding)
    except (ValueError, base64.binascii.Error) as e:
        raise InvalidCursorError(f"encoding: {e}") from e

    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise InvalidCursorError(f"payload: {e}") from e

    if not isinstance(decoded, dict):
        raise InvalidCursorError("payload: expected object")

    sort_field = decoded.get("s")
    value = decoded.get("v")
    id = decoded.get("id")

    if not isinstance(sort_field, str) or not isinstance(value, str) or not isinstance(id, str):
        raise InvalidCursorError("payload: missing or non-string s/v/id")

    if id == "":
        raise InvalidCursorError("missing id")
    if len(id) > MAX_CURSOR_ID_LENGTH:
        raise InvalidCursorError("id exceeds maximum length")
    if len(value) > MAX_CURSOR_VALUE_LENGTH:
        raise InvalidCursorError("value exceeds maximum length")

    if sort_field not in allowed_sort_fields:
        raise InvalidCursorError(f"unsupported sort field {sort_field!r}")

    return CursorPayload(sort_field=sort_field, value=value, id=id)


def decode_cursor_time(payload: Optional[CursorPayload]) -> datetime:
    """Parse a time-typed cursor value as Unix microseconds, returning UTC."""
    if payload is None:
        raise InvalidCursorError("nil cursor payload")
    try:
        micros = int(payload.value)
    except ValueError as e:
        raise InvalidCursorError(f"value is not a valid timestamp: {e}") from e
    return _unix_micros_to_datetime(micros)


def decode_cursor_int(payload: Optional[CursorPayload]) -> int:
    """Parse a cursor value as a base-10 integer."""
    if payload is None:
        raise InvalidCursorError("nil cursor payload")
    try:
        return int(payload.value)
    except ValueError as e:
        raise InvalidCursorError(f"value is not a valid integer: {e}") from e


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _datetime_to_unix_micros(t: datetime) -> int:
    """Convert an aware UTC datetime to Unix microseconds (integer math)."""
    delta = t - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def _unix_micros_to_datetime(micros: int) -> datetime:
    """Convert Unix microseconds to a UTC datetime, preserving precision."""
    seconds, micro_remainder = divmod(micros, 1_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=micro_remainder)
