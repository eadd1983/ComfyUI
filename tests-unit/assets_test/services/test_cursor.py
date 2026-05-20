"""Tests for app.assets.services.cursor.

The wire format must stay byte-identical with the cloud Go implementation
(common/pagination/cursor.go in Comfy-Org/cloud) so the frontend sees one
contract across runtimes. The byte-identity fixture below mirrors the Go
test cases — any drift here means cloud and OSS minted different cursors
for the same triple, which would break FE pagination across backends.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest

from app.assets.services.cursor import (
    MAX_CURSOR_ID_LENGTH,
    MAX_CURSOR_VALUE_LENGTH,
    MAX_ENCODED_CURSOR_LENGTH,
    CursorPayload,
    InvalidCursorError,
    decode_cursor,
    decode_cursor_int,
    decode_cursor_time,
    encode_cursor,
    encode_cursor_from_time,
)


ALLOWED = ("created_at", "updated_at", "name", "size")


class TestRoundTrip:
    @pytest.mark.parametrize(
        "sort_field, value, id",
        [
            ("created_at", "1716200000000000", "a1b2c3d4-e5f6-7a89-b0c1-d2e3f4a5b6c7"),
            ("size", "1024", "asset-123"),
            ("name", "my-asset.png", "asset-abc"),
            ("name", "résumé.txt", "asset-uni"),
        ],
    )
    def test_encode_decode(self, sort_field, value, id):
        encoded = encode_cursor(sort_field, value, id)
        assert encoded != ""
        payload = decode_cursor(encoded, ALLOWED)
        assert payload.sort_field == sort_field
        assert payload.value == value
        assert payload.id == id


class TestTimeCursor:
    def test_microsecond_precision_preserved(self):
        # Pick a time with non-zero microseconds — encoding at ms would lose the µs.
        ts = datetime(2024, 5, 20, 12, 53, 20, 123456, tzinfo=timezone.utc)
        encoded = encode_cursor_from_time("created_at", ts, "id-1")
        payload = decode_cursor(encoded, ALLOWED)
        # Value must be a microsecond integer string, not a millisecond one.
        assert payload.value == "1716209600123456"
        decoded = decode_cursor_time(payload)
        assert decoded == ts

    def test_decode_returns_utc(self):
        payload = CursorPayload(sort_field="created_at", value="1716200000123456", id="id-1")
        decoded = decode_cursor_time(payload)
        assert decoded.tzinfo == timezone.utc

    def test_naive_datetime_rejected_on_encode(self):
        naive = datetime(2024, 5, 20, 12, 0, 0)
        with pytest.raises(ValueError):
            encode_cursor_from_time("created_at", naive, "id-1")

    def test_non_integer_value_rejected_on_decode(self):
        with pytest.raises(InvalidCursorError):
            decode_cursor_time(CursorPayload("created_at", "not-a-number", "id-1"))

    def test_none_payload_rejected(self):
        with pytest.raises(InvalidCursorError):
            decode_cursor_time(None)

    def test_non_utc_aware_normalized(self):
        # Same instant, different timezone — must encode to the same micros.
        utc_ts = datetime(2024, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        offset_ts = utc_ts.astimezone(timezone(timedelta(hours=-5)))
        assert encode_cursor_from_time("created_at", utc_ts, "x") == encode_cursor_from_time(
            "created_at", offset_ts, "x"
        )


class TestIntCursor:
    def test_decode_int(self):
        assert decode_cursor_int(CursorPayload("size", "1024", "id-1")) == 1024

    def test_decode_int_rejects_non_int(self):
        with pytest.raises(InvalidCursorError):
            decode_cursor_int(CursorPayload("size", "abc", "id-1"))

    def test_decode_int_rejects_none(self):
        with pytest.raises(InvalidCursorError):
            decode_cursor_int(None)


class TestInvalidInputs:
    def test_oversized_cursor(self):
        oversized = "a" * (MAX_ENCODED_CURSOR_LENGTH + 1)
        with pytest.raises(InvalidCursorError, match="maximum length"):
            decode_cursor(oversized, ALLOWED)

    def test_not_base64(self):
        with pytest.raises(InvalidCursorError):
            decode_cursor("not base64!!!", ALLOWED)

    def test_not_json(self):
        encoded = base64.urlsafe_b64encode(b"definitely not json").rstrip(b"=").decode("ascii")
        with pytest.raises(InvalidCursorError):
            decode_cursor(encoded, ALLOWED)

    def test_empty_id(self):
        encoded = encode_cursor("created_at", "1", "")
        with pytest.raises(InvalidCursorError, match="missing id"):
            decode_cursor(encoded, ALLOWED)

    def test_oversized_id(self):
        encoded = encode_cursor("created_at", "1", "a" * (MAX_CURSOR_ID_LENGTH + 1))
        with pytest.raises(InvalidCursorError, match="id exceeds maximum length"):
            decode_cursor(encoded, ALLOWED)

    def test_oversized_value(self):
        encoded = encode_cursor("created_at", "v" * (MAX_CURSOR_VALUE_LENGTH + 1), "id-1")
        with pytest.raises(InvalidCursorError, match="value exceeds maximum length"):
            decode_cursor(encoded, ALLOWED)

    def test_unsupported_sort_field(self):
        encoded = encode_cursor("execution_time", "1", "id-1")
        with pytest.raises(InvalidCursorError, match="unsupported sort field"):
            decode_cursor(encoded, ALLOWED)

    def test_no_allowed_fields_rejects_everything(self):
        encoded = encode_cursor("created_at", "1", "id-1")
        with pytest.raises(InvalidCursorError):
            decode_cursor(encoded, ())

    def test_non_dict_payload_rejected(self):
        encoded = base64.urlsafe_b64encode(b'["array","not","dict"]').rstrip(b"=").decode("ascii")
        with pytest.raises(InvalidCursorError, match="expected object"):
            decode_cursor(encoded, ALLOWED)


class TestEncodeAtCapsFits:
    def test_max_field_lengths_fit_wire_cap(self):
        # Worst-case payload: value and id at their per-field caps, with a long
        # sort field name. The encoded cursor must fit within MAX_ENCODED_CURSOR_LENGTH
        # so the wire cap cannot reject a cursor the encoder mints at the per-field caps.
        value = "v" * MAX_CURSOR_VALUE_LENGTH
        id = "i" * MAX_CURSOR_ID_LENGTH
        sort_field = "very_long_sort_field_name"

        encoded = encode_cursor(sort_field, value, id)
        assert len(encoded) <= MAX_ENCODED_CURSOR_LENGTH
        payload = decode_cursor(encoded, (sort_field,))
        assert payload.value == value
        assert payload.id == id


class TestByteIdentityWithCloud:
    """Lock the wire format against drift from cloud's Go implementation.

    Drop these fixtures from common/pagination/cursor_test.go in cloud — they
    encode to specific base64url strings, and any drift on either side breaks
    cross-runtime FE pagination.

    To regenerate, run cloud's test harness with these inputs and capture the
    output of EncodeCursor, then paste below.
    """

    @pytest.mark.parametrize(
        "sort_field, value, id, expected_encoded",
        [
            # Generated from cloud encode_cursor: json.Marshal yields keys in
            # insertion order for our struct (s, v, id), then RawURLEncoding base64.
            ("created_at", "1716200000000000", "a1b2c3d4-e5f6-7a89-b0c1-d2e3f4a5b6c7",
             "eyJzIjoiY3JlYXRlZF9hdCIsInYiOiIxNzE2MjAwMDAwMDAwMDAwIiwiaWQiOiJhMWIyYzNkNC1lNWY2LTdhODktYjBjMS1kMmUzZjRhNWI2YzcifQ"),
            ("size", "1024", "asset-123",
             "eyJzIjoic2l6ZSIsInYiOiIxMDI0IiwiaWQiOiJhc3NldC0xMjMifQ"),
            ("name", "my-asset.png", "asset-abc",
             "eyJzIjoibmFtZSIsInYiOiJteS1hc3NldC5wbmciLCJpZCI6ImFzc2V0LWFiYyJ9"),
        ],
    )
    def test_python_matches_cloud_wire_bytes(self, sort_field, value, id, expected_encoded):
        actual = encode_cursor(sort_field, value, id)
        assert actual == expected_encoded, (
            f"Python cursor diverged from cloud Go wire format. "
            f"Got: {actual!r}, expected: {expected_encoded!r}"
        )
