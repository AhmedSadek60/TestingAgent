"""Identifier and time helpers."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime


def new_id() -> str:
    return str(uuid.uuid4())


def short_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: str | datetime) -> datetime:
    """A timestamp as an aware UTC datetime. Stored rows hand timestamps back as ISO text, with an offset or without one
    depending on the database; a moment with no offset is UTC wall-clock time."""
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    return stamp.astimezone(UTC) if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
