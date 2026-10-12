"""Passwords: a salted, memory-hard hash, and the rules a new password must meet.

``hashlib.scrypt`` (standard library) with a random salt per password. The parameters travel with the hash
(``scrypt$N$r$p$salt$hash``), so they can be raised later and old hashes still verify; ``needs_rehash`` says when a hash was
made with weaker parameters than today's. A hash that names absurd parameters (a tampered row) is refused instead of being
computed, because computing it would be a way to stall the server."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

N, R, P, DKLEN = 2**15, 8, 1, 32
MAXMEM = 128 * 1024 * 1024
MAX_N, MAX_R, MAX_P = 2**20, 16, 4
MAX_PASSWORD_CHARS = 256

#: A short list of the passwords everyone tries first. A user-supplied list can be added later; this stops the worst.
COMMON = frozenset(
    p.casefold()
    for p in [
        "password",
        "password1",
        "password12",
        "password123",
        "password1234",
        "passw0rd",
        "p@ssw0rd",
        "p@ssword",
        "123456",
        "1234567",
        "12345678",
        "123456789",
        "1234567890",
        "12345678910",
        "123456789012",
        "qwerty",
        "qwerty123",
        "qwertyuiop",
        "qwertyuiop123",
        "1q2w3e4r",
        "1q2w3e4r5t",
        "1qaz2wsx",
        "abc123",
        "abcd1234",
        "abcdef123456",
        "letmein",
        "letmein123",
        "welcome",
        "welcome1",
        "welcome123",
        "admin",
        "admin123",
        "administrator",
        "changeme",
        "changeme123",
        "iloveyou",
        "iloveyou123",
        "monkey",
        "dragon",
        "football",
        "baseball",
        "sunshine",
        "master",
        "shadow",
        "superman",
        "trustno1",
        "whatever",
        "starwars",
        "hello123",
        "default",
        "secret",
        "secret123",
        "test1234",
        "testtest",
        "test123456",
        "agentlab",
        "agentlab123",
        "agentlab1234",
        "agentlabagentlab",
        "letmein1234",
        "zaq12wsx",
        "login123",
        "passpass111",
        "000000000000",
        "111111111111",
    ]
)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, n: int = N, r: int = R, p: int = P) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=DKLEN, maxmem=MAXMEM)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(digest)}"


def _parse(encoded: str) -> tuple[int, int, int, bytes, bytes] | None:
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
        params = (int(n), int(r), int(p), _unb64(salt), _unb64(digest))
    except (ValueError, TypeError):
        return None
    n_, r_, p_, salt_, digest_ = params
    if (
        scheme != "scrypt"
        or not (1 < n_ <= MAX_N and n_ & (n_ - 1) == 0)
        or not (1 <= r_ <= MAX_R)
        or not (1 <= p_ <= MAX_P)
    ):
        return None
    if len(salt_) < 8 or len(digest_) < 16:
        return None
    return params


def verify_password(password: str, encoded: str) -> bool:
    parsed = _parse(encoded)
    if parsed is None or len(password) > MAX_PASSWORD_CHARS:
        return False
    n, r, p, salt, expected = parsed
    try:
        got = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected), maxmem=MAXMEM)
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(got, expected)


def needs_rehash(encoded: str) -> bool:
    parsed = _parse(encoded)
    return parsed is None or parsed[:3] != (N, R, P)


# A hash to compare against when the user name does not exist, so that "no such user" costs the same time as "wrong password".
DUMMY_HASH = hash_password("agentlab-no-such-user", n=N, r=R, p=P)


def problems_with(password: str, *, username: str = "", min_length: int = 12) -> list[str]:
    """Why ``password`` is not acceptable, as sentences for the person choosing it. Empty means it is."""
    found: list[str] = []
    if len(password) < min_length:
        found.append(f"use at least {min_length} characters")
    if len(password) > MAX_PASSWORD_CHARS:
        found.append(f"use at most {MAX_PASSWORD_CHARS} characters")
    if password.strip() != password or not password.strip():
        found.append("do not start or end with a space, and do not use only spaces")
    folded = password.casefold()
    if username and len(username) >= 3 and username.casefold() in folded:
        found.append("do not include your user name")
    if folded in COMMON or folded.rstrip("0123456789!") in COMMON:
        found.append("this is one of the most common passwords: choose another")
    if len(set(password)) < 5:
        found.append("use more different characters")
    return found
