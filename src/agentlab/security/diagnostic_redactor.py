"""DiagnosticRedactor: what is masked before diagnostic or attached text is shown, kept, exported or shared.

``SecretRedactor`` (``security/redactor.py``) masks the secrets AgentLab itself handles, everywhere it persists anything.
Text that a *person* supplies, or that a system produced for them (a log file, a stack trace, a pasted request, an
environment dump), needs more: it holds headers and cookies, connection strings, e-mail addresses and phone numbers, and
secrets of kinds nobody registered. This class builds on the same redactor and adds, in order:

1. **Structured fields**: when the text is JSON (or JSON lines), the value under a sensitive key is masked and the finding's
   location is the JSON path.
2. **Headers**: ``Authorization: Bearer <value>`` keeps the header and the scheme and masks the credential, so a failure can
   still be understood; ``Cookie`` and ``Set-Cookie`` keep the cookie names.
3. **Assignments**: ``API_TOKEN=...`` in an environment dump or a command line, and ``?token=...`` in a URL, keep the name.
4. **Credentials in URLs** (``postgresql://user:password@host/db``) keep the scheme, user and host.
5. **Patterns**: every pattern of the process-wide ``SecretRedactor`` (including every registered secret value), then personal
   information (e-mail, phone, payment card numbers) and the owner's own rules.
6. **A second inspection** of the result: anything the patterns would still change, and long high-entropy tokens that look
   like keys. What it finds is masked as well, and the result is marked ``needs_review``: the text is not safe to send outside
   AgentLab until a person has looked at it.

Findings carry a *kind*, a *location* (``line 12``, ``$.request.headers.cookie``) and a count, never the value.

This is risk reduction, not a guarantee. A secret in a shape nothing here recognises passes, and a person may name something
sensitive that no rule can know. The status ``redacted`` means "patterns matched and were masked", not "safe".
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from agentlab.security.redactor import SENSITIVE_KEYS, SecretRedactor, get_redactor

Status = Literal["clean", "redacted", "needs_review"]

#: Headers whose whole value is a credential. An authorization-like header keeps its scheme.
CREDENTIAL_HEADERS = (
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
    "x-xsrf-token",
    "x-goog-api-key",
    "x-amz-security-token",
    "x-access-token",
)
#: A name that says its value is secret: for assignments, query strings and structured logs.
SECRET_NAME = (
    r"(?:[A-Za-z0-9_.\-]*(?:token|secret|passw(?:or)?d|pwd|api[_-]?key|apikey|private[_-]?key|credential|"  # noqa: S105
    r"session(?:[_-]?id)?|cookie|auth(?:orization)?|signature|dsn)[A-Za-z0-9_.\-]*|[A-Za-z0-9_]*_key)"
)
SECRET_NAME_RE = re.compile(rf"(?i)^{SECRET_NAME}$")
#: Keys under which a structured log or request holds a secret, beyond the redactor's own.
EXTRA_SENSITIVE_KEYS = re.compile(
    r"(?i)^(cookie|set-cookie|x-csrf-token|x-auth-token|x-xsrf-token|session|session[_-]?id|csrf[_-]?token|credentials?|"
    r"connection[_-]?string|database[_-]?url|db[_-]?password|dsn|private[_-]?key|client[_-]?secret|signature)$"
)

# Every pattern below is anchored so it can start only where a name or a scheme begins (a lookbehind, or `^`). A pattern that may
# start at every position of a long run of name characters re-reads the run from each one: quadratic, and the text it is run
# over is a person's file. tests/unit/test_diagnostic_redactor.py::test_no_pattern_is_quadratic holds them to it.
_HEADER = re.compile(
    r"(?im)^(?P<prefix>[ \t>]*(?P<name>" + "|".join(re.escape(h) for h in CREDENTIAL_HEADERS) + r")\s*[:=]\s*)"
    r"(?P<scheme>(?:bearer|basic|digest|token|negotiate|apikey|api-key)[ \t]+)?(?P<value>[^\r\n\"',;\ue000]{3,})"
)
_COOKIE = re.compile(r"(?im)^(?P<prefix>[ \t>]*(?:set-)?cookie\s*:\s*)(?P<value>[^\r\n]+)$")
_ENV = re.compile(
    rf"(?im)^(?P<prefix>[ \t]*(?:export[ \t]+|set[ \t]+|ENV[ \t]+)?(?P<name>{SECRET_NAME})[ \t]*=[ \t]*)"
    r"(?P<quote>[\"']?)(?P<value>[^\s\"'\ue000]{3,})"
)
_QUERY = re.compile(rf"(?i)(?P<prefix>[?&;](?P<name>{SECRET_NAME})=)(?P<value>[^&\s\"'<>#\ue000]{{3,}})")
_URL_CREDENTIALS = re.compile(
    r"(?P<scheme>(?<![A-Za-z0-9+.\-])[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^\s:/@\"'<>]+):(?P<value>[^\s@/\"'<>\ue000]+)(?P<at>@[^\s/\"'<>]+)"
)
_EMAIL = re.compile(r"(?<![\w.+\-])(?!git@)[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}(?![\w\-])")
_PHONE_INTL = re.compile(r"(?<![\w.])\+\d{1,3}[\s.\-]?(?:\(\d{1,4}\)[\s.\-]?)?\d{1,4}(?:[\s.\-]?\d{2,4}){2,4}(?![\w.])")
_PHONE_US = re.compile(r"(?<![\w.\-])\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?![\w.\-])")
_CARD = re.compile(r"(?<![\w.\-])(?:\d[ \-]?){12,18}\d(?![\w.\-])")
_IPV4 = re.compile(r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\w.])")
_TOKEN = re.compile(r"[A-Za-z0-9+/_\-]{24,}")
#: Private-use characters that stand in for a span that is set aside while the patterns run (see ``_shield``).
_OPEN, _CLOSE = "\ue000", "\ue001"
_HELD = re.compile(f"{_OPEN}(\\d+){_CLOSE}")
_UUID = re.compile(r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_HEX = re.compile(r"(?i)^[0-9a-f]+$")
_MARKER = re.compile(r"\[REDACTED:[A-Za-z0-9_.:\- ]+\]")
#: A name and the marker that stands for its value (``password=[REDACTED:x]``, ``Authorization: Bearer [REDACTED:x]``): the whole
#: span is set aside, because a pattern that does not know it is masked would take the marker for a value and the text after it.
_MASKED_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_.\-])[A-Za-z0-9_.\-]+[ \t]*[:=][ \t]*(?:(?:bearer|basic|digest|token|negotiate|apikey|api-key)[ \t]+)?[\"']?"
    r"\[REDACTED:[A-Za-z0-9_.:\- ]+\]",
    re.I,
)
#: A URL whose password was masked here: it still looks like ``user:secret@host`` to a pattern that does not know that.
_MASKED_URL = re.compile(
    r"(?<![A-Za-z0-9+.\-])[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@\"'<>]+:\[REDACTED:url_password\]@[^\s/\"'<>]+"
)
_KEY_BEGIN = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE" + r" KEY(?: BLOCK)?-----")
_KEY_END = re.compile(r"-----END (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE" + r" KEY(?: BLOCK)?-----")
KEY_BLOCK_LIMIT = 20_000  # the most a block may be, and how much of an unterminated one is masked


@dataclass(frozen=True)
class Finding:
    """What was masked and where; never the value."""

    kind: str
    location: str
    count: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "location": self.location, "count": self.count}


@dataclass
class DiagnosticResult:
    #: The masked text (for structured input, its JSON).
    text: str
    #: For structured input, the masked value itself.
    value: Any = None
    findings: list[Finding] = field(default_factory=list)  # masked by the first pass
    residual: list[Finding] = field(default_factory=list)  # found by the second inspection, and masked too
    mechanisms: list[str] = field(default_factory=list)

    @property
    def status(self) -> Status:
        if self.residual:
            return "needs_review"
        return "redacted" if self.findings else "clean"

    @property
    def external_sharing_blocked(self) -> bool:
        """True when the text may not leave AgentLab until a person has reviewed it."""
        return self.status == "needs_review"

    def counts(self) -> dict[str, int]:
        out: Counter[str] = Counter()
        for f in (*self.findings, *self.residual):
            out[f.kind] += f.count
        return dict(out)

    def report(self) -> dict[str, Any]:
        """What a person may be shown and what a manifest may hold: kinds, locations and counts."""
        return {
            "status": self.status,
            "external_sharing_blocked": self.external_sharing_blocked,
            "mechanisms": list(self.mechanisms),
            "redactions": [f.as_dict() for f in self.findings],
            "second_inspection": [f.as_dict() for f in self.residual],
            "counts": self.counts(),
            "note": "Redaction reduces risk; it does not guarantee that nothing sensitive remains.",
        }


def mask_private_keys(text: str) -> tuple[str, list[int]]:
    """Mask every private-key block of ``text``, in time linear in its length, and return it with the line each began on.

    A block runs from its BEGIN marker to the first END marker after it. One that has no END before the next BEGIN (a
    truncated paste) is masked as far as its blank line, the next BEGIN or ``KEY_BLOCK_LIMIT`` characters. Each marker is
    searched for once and an END is found once for all the BEGINs in front of it, so a file of a million BEGINs is no
    slower per character than a file with one. The masked block keeps its number of lines, so a later "line 40" in a report
    is line 40 of the file that was attached."""
    pieces: list[str] = []
    lines: list[int] = []
    last = pos = counted_to = 0
    line = 1
    end: re.Match[str] | None = None
    no_end_left = False
    while (begin := _KEY_BEGIN.search(text, pos)) is not None:
        following = _KEY_BEGIN.search(text, begin.end())
        if not no_end_left and (end is None or end.start() < begin.end()):
            end = _KEY_END.search(text, begin.end())
            no_end_left = end is None
        if (
            end is not None
            and (following is None or end.start() < following.start())
            and end.end() - begin.start() <= KEY_BLOCK_LIMIT
        ):
            stop = end.end()
        else:
            ceiling = begin.end() + KEY_BLOCK_LIMIT
            blank = text.find("\n\n", begin.end(), ceiling)
            stop = min(
                blank if blank != -1 else ceiling,
                following.start() if following is not None else len(text),
                len(text),
            )
        line += text.count("\n", counted_to, begin.start())
        counted_to = begin.start()
        lines.append(line)
        pieces.append(text[last : begin.start()])
        pieces.append("[REDACTED:private_key]" + "\n" * text.count("\n", begin.start(), stop))
        last = pos = stop
    pieces.append(text[last:])
    return "".join(pieces), lines


def _entropy(token: str) -> float:
    if not token:
        return 0.0
    n = len(token)
    return -sum(c / n * math.log2(c / n) for c in Counter(token).values())


def _luhn(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _shield_masked(text: str, held: list[str]) -> str:
    """Set aside every span that an earlier masking produced, most specific first."""
    for pattern in (_MASKED_URL, _MASKED_ASSIGNMENT, _MARKER):
        text = _shield(text, pattern, held)
    return text


def _shield(text: str, pattern: re.Pattern[str], held: list[str]) -> str:
    """Set the spans ``pattern`` finds aside, so later patterns do not see them. What an earlier masking produced (a marker, a
    URL that keeps its host) must not be masked again by a pattern that does not know it is a marker."""

    def hold(m: re.Match[str]) -> str:
        held.append(m.group(0))
        return f"{_OPEN}{len(held) - 1}{_CLOSE}"

    return pattern.sub(hold, text)


def _unshield(text: str, held: list[str]) -> str:
    return _HELD.sub(lambda m: held[int(m.group(1))], text)


class DiagnosticRedactor:
    """Masks text and structured data for review, storage and export. Thread-safe."""

    def __init__(
        self,
        *,
        base: SecretRedactor | None = None,
        custom_rules: Iterable[tuple[str, str]] = (),
        redact_ip_addresses: bool = False,
    ) -> None:
        self._base = base or get_redactor()
        self._custom = [(name, re.compile(pattern)) for name, pattern in custom_rules]
        self._ips = redact_ip_addresses

    # -------------------------------------------------------------------------------------------- the entry points
    def redact_text(self, text: str) -> DiagnosticResult:
        """Mask ``text``. A JSON document and JSON lines are handled by their structure; anything else line by line."""
        stripped = text.strip()
        if stripped[:1] in ("{", "["):
            try:
                value = json.loads(stripped)
            except ValueError:
                value = None
            else:
                return self.redact_value(value)
        lines = text.split("\n")
        structured_lines = stripped[:1] == "{" and len(lines) > 1
        if structured_lines:
            return self._json_lines(lines)
        masked, findings = self._multiline(text)
        result = DiagnosticResult(text=masked, findings=findings)
        self._inspect_again(result)
        result.mechanisms = self._mechanisms(result)
        return result

    def redact_value(self, value: Any) -> DiagnosticResult:
        """Mask a JSON-like value: sensitive keys by name, every string by the text rules."""
        findings: list[Finding] = []
        masked = self._walk(value, "$", None, findings)
        result = DiagnosticResult(text=json.dumps(masked, ensure_ascii=False), value=masked, findings=findings)
        self._inspect_again(result)
        if result.residual:  # the second inspection masked more in the serialised form: keep that
            result.value = json.loads(result.text)
        result.mechanisms = _unique(["structured fields", *self._mechanisms(result)])
        return result

    def inspect(self, text: str) -> list[Finding]:
        """What a second inspection would flag in ``text``, without changing it."""
        probe = DiagnosticResult(text=text)
        self._inspect_again(probe)
        return probe.residual

    # ------------------------------------------------------------------------------------------------ structure
    def _walk(self, v: Any, path: str, key: str | None, findings: list[Finding]) -> Any:
        named_secret = key is not None and (
            SENSITIVE_KEYS.match(key) or EXTRA_SENSITIVE_KEYS.match(key) or SECRET_NAME_RE.match(key)
        )
        if named_secret and v not in (None, "", [], {}) and not isinstance(v, dict | list):
            findings.append(Finding("sensitive_key", path))
            return "[REDACTED:sensitive_key]"
        if isinstance(v, str):
            clean, notes = self._text_pass(v, path)
            findings.extend(notes)
            return clean
        if isinstance(v, dict):
            return {k: self._walk(x, f"{path}.{k}", str(k), findings) for k, x in v.items()}
        if isinstance(v, list | tuple):
            return [self._walk(x, f"{path}[{i}]", None, findings) for i, x in enumerate(v)]
        return v

    def _json_lines(self, lines: list[str]) -> DiagnosticResult:
        findings: list[Finding] = []
        out: list[str] = []
        for number, line in enumerate(lines, 1):
            if not line.strip():
                out.append(line)
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                clean, notes = self._text_pass(line, f"line {number}")
                out.append(clean)
                findings.extend(notes)
                continue
            out.append(json.dumps(self._walk(obj, f"line {number} $", None, findings), ensure_ascii=False))
        result = DiagnosticResult(text="\n".join(out), findings=findings)
        self._inspect_again(result)
        result.mechanisms = _unique(["structured fields", *self._mechanisms(result)])
        return result

    def _multiline(self, text: str) -> tuple[str, list[Finding]]:
        findings: list[Finding] = []
        text, key_lines = mask_private_keys(text)  # a block that spans lines is found on the whole text
        findings.extend(Finding("private_key", f"line {n}") for n in key_lines)
        out: list[str] = []
        for number, line in enumerate(text.split("\n"), 1):
            clean, notes = self._text_pass(line, f"line {number}")
            out.append(clean)
            findings.extend(notes)
        return "\n".join(out), findings

    # -------------------------------------------------------------------------------------------------- patterns
    def _text_pass(self, text: str, where: str) -> tuple[str, list[Finding]]:
        """The ordered masking of one line or one string. ``where`` is its location."""
        findings: list[Finding] = []

        def sub(pattern: re.Pattern[str], repl: Callable[[re.Match[str]], str], kind: str, s: str) -> str:
            changed = 0

            def counted(m: re.Match[str]) -> str:
                nonlocal changed
                out = repl(m)
                if out != m.group(0):
                    changed += 1
                return out

            out = pattern.sub(counted, s)
            if changed:
                findings.append(Finding(kind, where, changed))
            return out

        def keep_name(label: str) -> Callable[[re.Match[str]], str]:
            return lambda m: f"{m.group('prefix')}{m.groupdict().get('quote') or ''}[REDACTED:{label}]"

        def header(m: re.Match[str]) -> str:
            scheme = (m.group("scheme") or "").strip()
            return f"{m.group('prefix')}{scheme + ' ' if scheme else ''}[REDACTED:{m.group('name').lower()}]"

        def cookie(m: re.Match[str]) -> str:
            masked = re.sub(
                r"(?P<n>[^=;\s]+)=(?P<v>[^;]*)",
                lambda c: c.group(0) if _OPEN in c.group("v") else f"{c.group('n')}=[REDACTED:cookie]",
                m.group("value"),
            )
            return f"{m.group('prefix')}{masked}"

        def masker(replacement: str) -> Callable[[re.Match[str]], str]:
            return lambda m: replacement

        held: list[str] = []

        def url_credentials(m: re.Match[str]) -> str:
            findings.append(Finding("url_credentials", where))
            held.append(f"{m.group('scheme')}{m.group('user')}:[REDACTED:url_password]{m.group('at')}")
            return f"{_OPEN}{len(held) - 1}{_CLOSE}"

        valid_cards = 0

        def card(m: re.Match[str]) -> str:
            nonlocal valid_cards
            if _luhn(m.group()):
                valid_cards += 1
                return "[REDACTED:card_number]"
            return m.group()

        # what an earlier masking produced is not masked a second time
        text = _shield_masked(text, held)
        text = sub(_HEADER, header, "credential_header", text)
        text = sub(_COOKIE, cookie, "cookie", text)
        text = _URL_CREDENTIALS.sub(url_credentials, text)
        text = sub(_QUERY, keep_name("query_secret"), "query_secret", text)
        text = sub(_ENV, keep_name("env_secret"), "secret_assignment", text)
        text = _shield_masked(text, held)  # the markers of the steps above, too
        text, hits = self._base.redact_text(text)  # every pattern and every registered value of the process
        findings.extend(Finding(kind, where, n) for kind, n in hits.items())
        text = sub(_EMAIL, lambda m: "[REDACTED:email]", "email", text)
        text = sub(_PHONE_INTL, lambda m: "[REDACTED:phone]", "phone", text)
        text = sub(_PHONE_US, lambda m: "[REDACTED:phone]", "phone", text)
        text = _CARD.sub(card, text)
        if valid_cards:
            findings.append(Finding("card_number", where, valid_cards))
        if self._ips:
            text = sub(_IPV4, lambda m: "[REDACTED:ip_address]", "ip_address", text)
        for name, pattern in self._custom:
            text = sub(pattern, masker(f"[REDACTED:{name}]"), f"custom:{name}", text)
        return _unshield(text, held), findings

    # ------------------------------------------------------------------------------------- the second inspection
    def _inspect_again(self, result: DiagnosticResult) -> None:
        """Look again at the masked text. What is found is masked too and recorded as residual: the first pass did not
        establish that the text is clean, so it needs a person's review before it leaves AgentLab."""
        found: dict[tuple[str, str], int] = {}
        # (a) anything the known patterns would still change (markers are set aside: they are what the first pass left)
        held: list[str] = []
        text, hits = self._base.redact_text(_shield_masked(result.text, held))
        text = _unshield(text, held)
        for kind, n in hits.items():
            found[(kind, "text")] = found.get((kind, "text"), 0) + n
        # (b) long tokens that look like keys: mixed character classes and high entropy, or any entropy next to a name
        lines = text.split("\n")
        for number, line in enumerate(lines, 1):
            plain = _MARKER.sub(" ", line)
            for m in _TOKEN.finditer(plain):
                token = m.group()
                if self._looks_like_a_key(token, plain[max(0, m.start() - 40) : m.start()]):
                    line = line.replace(token, "[REDACTED:high_entropy]")
                    key = ("high_entropy_token", f"line {number}")
                    found[key] = found.get(key, 0) + 1
            lines[number - 1] = line
        if found:
            result.text = "\n".join(lines)
        result.residual = [Finding(kind, loc, n) for (kind, loc), n in found.items()]

    @staticmethod
    def _looks_like_a_key(token: str, before: str) -> bool:
        core = token.strip("=-_/+")
        if len(core) < 24 or _UUID.match(core):
            return False
        near_name = bool(re.search(rf"(?i){SECRET_NAME}\W{{0,3}}$", before))
        if _HEX.match(core) and len(core) in (32, 40, 64) and not near_name:
            return False  # a digest or an identifier, which logs are full of
        has_digit = any(c.isdigit() for c in core)
        has_alpha = any(c.isalpha() for c in core)
        mixed_case = any(c.islower() for c in core) and any(c.isupper() for c in core)
        entropy = _entropy(core)
        if near_name:
            return has_digit and has_alpha and entropy >= 3.2
        return len(core) >= 32 and has_digit and has_alpha and mixed_case and entropy >= 4.0

    @staticmethod
    def _mechanisms(result: DiagnosticResult) -> list[str]:
        kinds = {f.kind for f in result.findings}
        names = ["patterns"]
        if kinds & {"credential_header", "cookie"}:
            names.append("headers")
        if kinds & {"secret_assignment", "query_secret", "url_credentials"}:
            names.append("assignments")
        if kinds & {"email", "phone", "card_number", "ip_address"}:
            names.append("personal information")
        if any(k.startswith("custom:") for k in kinds):
            names.append("custom rules")
        names.append("second inspection")
        return names
