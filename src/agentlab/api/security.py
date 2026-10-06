"""Protecting the API itself (spec sections 11, 35 and 41).

AgentLab can clone repositories, start containers, drive a browser and send requests to arbitrary addresses, so whoever can
call its API can make the server do those things. The protections here are layered:

* the server listens on loopback unless told otherwise, and refuses to listen anywhere else without a token;
* a token, compared in constant time, when one is configured (failed attempts are slowed down);
* when there is no token (loopback only), the ``Host`` header must name this machine, which defeats DNS rebinding, and a
  request that a *browser page of another origin* sends is refused, which defeats cross-site requests;
* a request can point a target at a file on the server only inside the folders the operator allowed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agentlab.core.errors import PolicyBlocked
from agentlab.security.credentials import CredentialManager, resolve_reference

LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_FAILURES = 10
FAILURE_WINDOW_SECONDS = 60.0


# ======================================================================================================== token
def is_loopback(host: str) -> bool:
    """Whether ``host`` can only be reached from this machine."""
    name = host.strip("[]").lower()
    if name in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def resolve_token(ref: str | None, credentials: CredentialManager | None) -> str | None:
    """The API token named by ``env:NAME`` or ``secret:NAME`` (``None`` when no token is configured)."""
    if not ref:
        return None
    if ref.startswith("secret:"):
        if credentials is None:
            raise PolicyBlocked("server.token_ref names a stored credential but no credential store is available")
        fields = credentials.fields(ref.split(":", 1)[1])
        token = fields.get("token") or fields.get("key")
    else:
        token = resolve_reference(ref)
    if not token or len(token) < 16:
        raise PolicyBlocked("the API token must be at least 16 characters; generate one with `openssl rand -hex 24`")
    return token


class TokenGuard:
    """Checks the presented token and slows down a client that keeps getting it wrong."""

    def __init__(self, token: str | None) -> None:
        self._token = token.encode() if token else None
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    @property
    def required(self) -> bool:
        return self._token is not None

    def throttled(self, client: str) -> bool:
        attempts = self._failures[client]
        cutoff = time.monotonic() - FAILURE_WINDOW_SECONDS
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return len(attempts) >= MAX_FAILURES

    def check(self, client: str, presented: str | None) -> bool:
        if self._token is None:
            return True
        if presented is None:
            return False  # nothing was guessed (a page that has not asked for the token yet), so nothing is counted
        ok = hmac.compare_digest(presented.encode(), self._token)
        if not ok:
            self._failures[client].append(time.monotonic())
            if len(self._failures) > 4096:  # an attacker with many addresses must not grow this without bound
                self._failures.pop(next(iter(self._failures)))
        return ok


def presented_token(headers: Any) -> str | None:
    """``Authorization: Bearer TOKEN`` or ``X-API-Key: TOKEN``."""
    auth = headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return str(auth[7:]).strip() or None
    key = headers.get("x-api-key")
    return str(key).strip() if key else None


# ================================================================================================ middleware
def _host_name(value: str) -> str:
    try:
        return (urlsplit("//" + value).hostname or "").lower()
    except ValueError:
        return ""


def _origin_of(value: str) -> str:
    parts = urlsplit(value)
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else ""


class RequestGuard:
    """Pure ASGI middleware (it must not buffer the live event stream).

    * ``allowed_hosts`` (when given): the ``Host`` header must name one of them. Used without a token, so a web page cannot
      reach the API by pointing its own domain at 127.0.0.1.
    * An unsafe request (POST, PUT, PATCH, DELETE) that carries an ``Origin`` header must come from this server's own origin
      or one the operator listed: a form on another site can send a request to a loopback server, but it cannot hide its
      origin. Requests without an ``Origin`` (curl, the CLI, other programs) are not browser pages and are not affected.
    * Security headers on every response.
    """

    def __init__(self, app: ASGIApp, *, allowed_hosts: Sequence[str] | None, allowed_origins: Sequence[str]) -> None:
        self.app = app
        self.allowed_hosts = {h.lower() for h in allowed_hosts} if allowed_hosts is not None else None
        self.allowed_origins = {o.rstrip("/").lower() for o in allowed_origins}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = _host_name(headers.get("host", ""))
        if self.allowed_hosts is not None and host not in self.allowed_hosts:
            await self._refuse(
                send, 400, "host_not_allowed", f"this server answers to {', '.join(sorted(self.allowed_hosts))}"
            )
            return
        origin = headers.get("origin")
        if origin and scope["method"] in UNSAFE_METHODS:
            same = _origin_of(origin) in {
                f"http://{headers.get('host', '')}".lower(),
                f"https://{headers.get('host', '')}".lower(),
            }
            if not same and origin.rstrip("/").lower() not in self.allowed_origins:
                await self._refuse(send, 403, "origin_not_allowed", "requests from this web origin are not allowed")
                return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw = list(message.get("headers", []))
                present = {k.lower() for k, _ in raw}
                for key, value in SECURITY_HEADERS:
                    if key.lower().encode() not in present:  # header names go over the wire in lower case
                        raw.append((key.encode(), value.encode()))
                message["headers"] = raw
            await send(message)

        await self.app(scope, receive, with_headers)

    @staticmethod
    async def _refuse(send: Send, status: int, kind: str, message: str) -> None:
        body = json.dumps({"error": {"kind": kind, "message": message}}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)

# The web interface is served by the API and needs nothing from anywhere else. ``frame-src 'self'`` is for the report
# viewer, which shows a report in a sandboxed frame from a short-lived link of this server (see ``LinkSigner``).
UI_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self' data:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'"
)
# A document that came out of a run (a report, a page snapshot) can contain anything a target said. It is shown in a frame
# with no origin of its own: it cannot read the interface's storage or call the API as the person using it.
SANDBOX_CSP = (
    "sandbox allow-scripts; default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:"
)
# The same document inside the interface's frame: only this server's own pages may frame it.
FRAMED_CSP = SANDBOX_CSP + "; frame-ancestors 'self'"


# ================================================================================================= view links
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class LinkSigner:
    """Short-lived links to one file of one report.

    A report is shown in a frame, and a frame cannot send the ``Authorization`` header, so the interface asks for a link that
    carries its own proof: the report, the format and an expiry, signed with a key that exists only in this process's memory.
    The link opens that one file and nothing else; it stops working after ``ttl_seconds`` and when the server restarts."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds
        self._key = secrets.token_bytes(32)

    def _mac(self, payload: bytes) -> bytes:
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    def sign(self, report_id: str, fmt: str, *, now: float | None = None) -> str:
        expires = int((time.time() if now is None else now) + self.ttl_seconds)
        payload = json.dumps({"r": report_id, "f": fmt, "e": expires}, separators=(",", ":")).encode()
        return f"{_b64(payload)}.{_b64(self._mac(payload))}"

    def verify(self, token: str, *, now: float | None = None) -> tuple[str, str] | None:
        """``(report id, format)`` when the link is genuine and has not expired, else ``None``."""
        if not token or len(token) > 1024 or token.count(".") != 1:
            return None
        try:
            body, mac = token.split(".")
            payload, presented = _unb64(body), _unb64(mac)
            # Base64 ignores the spare bits of its last character, so two different strings can decode to the same
            # bytes. Only the exact string this signer wrote is a link; anything else is refused.
            if _b64(payload) != body or _b64(presented) != mac:
                return None
            if not hmac.compare_digest(presented, self._mac(payload)):
                return None
            claims = json.loads(payload)
            report, fmt, expires = str(claims["r"]), str(claims["f"]), int(claims["e"])
        except (ValueError, KeyError, TypeError):
            return None
        if (time.time() if now is None else now) >= expires:
            return None
        return report, fmt


# ===================================================================================================== paths
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ \-]+")


def safe_filename(name: str, *, fallback: str = "file") -> str:
    """A file name that cannot name another place: only the last component, no control or special characters."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = SAFE_NAME.sub("_", base).strip(" .")
    base = re.sub(r"\.{2,}", ".", base)[:120]
    return base or fallback


def confine(raw: str, roots: Sequence[Path], *, what: str) -> Path:
    """``raw`` as an absolute path, if (after following links) it is inside one of ``roots``.

    The API may be reached by people who must not be able to read arbitrary files by naming them in a target, so a path
    in a request is accepted only under the folders the operator listed in ``server.allowed_paths`` (and the uploads)."""
    try:
        resolved = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PolicyBlocked(f"{what} '{raw[:80]}' is not a usable path") from exc
    for root in roots:
        try:
            if resolved.is_relative_to(root.resolve()):
                return resolved
        except OSError:
            continue
    if not roots:
        raise PolicyBlocked(
            f"{what} names a path on the server, and this server allows none. Upload the file instead, or list a "
            "folder under server.allowed_paths"
        )
    raise PolicyBlocked(f"{what} '{raw[:80]}' is outside the folders this server allows (server.allowed_paths)")
