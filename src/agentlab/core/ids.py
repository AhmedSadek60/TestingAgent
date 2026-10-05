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


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
