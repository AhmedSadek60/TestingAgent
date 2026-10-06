"""SecretRedactor (spec section 11).

Redaction is applied before anything is persisted: traces, logs, artifacts, reports,
database records and exception messages. It combines

* registered secret *values* (every credential resolved by the CredentialManager),
* built-in patterns for common secret shapes, and
* user-defined patterns from configuration,

and masks whole values under sensitive keys (``authorization``, ``password``, ...).
"""

from __future__ import annotations

import base64
import logging
import re
import threading
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from agentlab.core.enums import RedactionStatus

_PRIVATE_KEY_BEGIN = "-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE" + " KEY-----"
_PRIVATE_KEY_END = "-----END (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE" + " KEY-----"

BUILTIN_PATTERNS: dict[str, str] = {
    "private_key": _PRIVATE_KEY_BEGIN + r"[\s\S]+?" + _PRIVATE_KEY_END,
    "aws_access_key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "aws_secret_key": r"(?i)(aws_secret_access_key|aws_secret)\s*[:=]\s*\\*['\"]?[A-Za-z0-9/+=]{40}",
    "jwt": r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
    "bearer": r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{12,}=*",
    "basic_auth": r"(?i)\bbasic\s+[A-Za-z0-9+/]{12,}=*",
    "github_token": r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b",
    "slack_token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b",
    "google_api_key": r"\bAIza[0-9A-Za-z_\-]{35}\b",
    "anthropic_key": r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b",
    "openrouter_key": r"\bsk-or-[A-Za-z0-9_\-]{20,}\b",
    "openai_key": r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b",
    "connection_string": r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+:[^\s@/]+@[^\s/]+",
    # the value may sit in JSON text, where its quotes are escaped (``\"``): the backslashes are part of the quote, not of it
    "password_assignment": r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token)\b"
    r"(\s*[:=]\s*)(\\*['\"]?)[^\s'\"\\,;]{4,}",
}

SENSITIVE_KEYS = re.compile(
    r"(?i)^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api[_-]?key|apikey|password|"
    r"passwd|secret|client[_-]?secret|access[_-]?token|refresh[_-]?token|id[_-]?token|token|private[_-]?key"
    r"|x-goog-api-key|session[_-]?token|storage[_-]?state)$"
)


@dataclass
class RedactionResult:
    value: Any
    status: RedactionStatus
    hits: dict[str, int]


def _keep_assignment(label: str) -> Callable[[re.Match[str]], str]:
    """Replacement that keeps ``name = `` and quotes of a password assignment but hides the value."""

    def repl(m: re.Match[str]) -> str:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED:{label}]"

    return repl


class SecretRedactor:
    """Thread-safe redactor. One instance is shared per process (see :func:`get_redactor`)."""

    MIN_SECRET_LEN = 6

    def __init__(self, extra_patterns: Iterable[str] = ()) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}
        self._patterns: dict[str, re.Pattern[str]] = {k: re.compile(v) for k, v in BUILTIN_PATTERNS.items()}
        for i, p in enumerate(extra_patterns):
            self.add_pattern(f"custom_{i}", p)

    def add_pattern(self, label: str, pattern: str) -> None:
        with self._lock:
            self._patterns[label] = re.compile(pattern)

    def register_secret(self, value: str | None, label: str = "credential") -> None:
        """Register an exact secret value (and its common encodings) for masking."""
        if not value or len(value) < self.MIN_SECRET_LEN:
            return
        variants = {value, urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value)}
        try:
            variants.add(base64.b64encode(value.encode()).decode())
        except Exception:  # noqa: S110 - best effort
            pass
        with self._lock:
            for v in variants:
                if len(v) >= self.MIN_SECRET_LEN:
                    self._values[v] = label

    def redact_text(self, text: str) -> tuple[str, dict[str, int]]:
        hits: dict[str, int] = {}
        with self._lock:
            values = sorted(self._values.items(), key=lambda kv: -len(kv[0]))
            patterns = list(self._patterns.items())
        for secret, label in values:
            if secret in text:
                hits[label] = hits.get(label, 0) + text.count(secret)
                text = text.replace(secret, f"[REDACTED:{label}]")
        for label, pat in patterns:
            if label == "password_assignment":
                text, n = pat.subn(_keep_assignment(label), text)
            else:
                text, n = pat.subn(f"[REDACTED:{label}]", text)
            if n:
                hits[label] = hits.get(label, 0) + n
        return text, hits

    def redact(self, value: Any) -> RedactionResult:
        hits: dict[str, int] = {}

        def _walk(v: Any, key: str | None = None) -> Any:
            if key is not None and SENSITIVE_KEYS.match(key) and v not in (None, "", [], {}):
                hits["sensitive_key"] = hits.get("sensitive_key", 0) + 1
                return "[REDACTED:sensitive_key]"
            if isinstance(v, str):
                out, h = self.redact_text(v)
                for k, n in h.items():
                    hits[k] = hits.get(k, 0) + n
                return out
            if isinstance(v, dict):
                return {k: _walk(x, str(k)) for k, x in v.items()}
            if isinstance(v, list | tuple):
                return [_walk(x) for x in v]
            return v

        out = _walk(value)
        status = RedactionStatus.REDACTED if hits else RedactionStatus.CLEAN
        return RedactionResult(out, status, hits)

    def contains_secret(self, text: str) -> bool:
        return bool(self.redact_text(text)[1])


_default: SecretRedactor | None = None
_default_lock = threading.Lock()


def get_redactor() -> SecretRedactor:
    global _default
    with _default_lock:
        if _default is None:
            _default = SecretRedactor()
        return _default


def redact(value: Any) -> Any:
    return get_redactor().redact(value).value


def redact_strings(value: Any) -> Any:
    """Mask secrets in every string *value* of a JSON-like structure and leave keys and structure alone.

    This is what a database column needs: a tool schema may have a parameter called ``password`` and a credential
    profile a field called ``token``; hiding those *names* would destroy the record, while a secret *value* under any key
    must never be written."""
    redactor = get_redactor()

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            return redactor.redact_text(v)[0]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list | tuple):
            return [walk(x) for x in v]
        return v

    return walk(value)


class RedactingLogFilter(logging.Filter):
    """Masks secrets in every log record (message and traceback) before a handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg, record.args = redact(record.getMessage()), None
            if record.exc_info:
                record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
                record.exc_info = None
        except Exception:  # logging must never fail because redaction did
            record.msg, record.args = "[log record withheld: it could not be redacted]", None
        return True
