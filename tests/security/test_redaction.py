"""A secret that reaches a log, a trace, a database row, an artifact or a report has leaked, however briefly (spec section 11).

The redactor is the last line of defence, so it is tested on the shapes real secrets take, on the strings that merely look
like them (a mask that eats ordinary text makes reports useless), and on every sink it guards."""

from __future__ import annotations

import base64
import io
import json
import logging
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import JSON, Column, Integer, Text, create_engine, text
from sqlalchemy.orm import declarative_base

from agentlab.core.enums import RedactionStatus
from agentlab.security.canary import CanaryRegistry
from agentlab.security.redactor import (
    BUILTIN_PATTERNS,
    RedactingLogFilter,
    SecretRedactor,
    redact,
    redact_strings,
)
from agentlab.storage.orm import RedactedJSON, RedactedText

# Fake credentials, assembled from fragments so that no secret scanner mistakes this file for a leak.
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
JWT = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
GITHUB = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
SLACK = "xoxb-" + "1234567890-abcdefghijkl"
GOOGLE = "AIza" + "SyDfakefakefakefakefakefakefake1234"
ANTHROPIC = "sk-ant-" + "api03-fakefakefakefakefakefake"
OPENROUTER = "sk-or-v1-" + "fakefakefakefakefakefakefake"
OPENAI = "sk-" + "proj-fakefakefakefakefakefake1234"
PRIVATE_KEY = "-----BEGIN " + "PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END " + "PRIVATE KEY-----"
AWS_SECRET = "aws_secret_access_key = " + "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
CONNECTION = "postgres://app:" + "hunter2-hunter2@db.internal:5432/app"

SHAPES = {
    "private_key": PRIVATE_KEY,
    "aws_access_key": f"key id {AWS_KEY} in a sentence",
    "aws_secret_key": AWS_SECRET,
    "jwt": f"token={JWT}",
    "bearer": "Authorization: Bearer " + "abcdefghijklmnop1234567890",
    "basic_auth": "Authorization: Basic " + "dXNlcjpwYXNzd29yZDEyMzQ1Ng==",
    "github_token": f"clone with {GITHUB}",
    "slack_token": f"post with {SLACK}",
    "google_api_key": f"?key={GOOGLE}",
    "anthropic_key": f"ANTHROPIC={ANTHROPIC}",
    "openrouter_key": f"or {OPENROUTER}",
    "openai_key": f"OPENAI_API_KEY={OPENAI}",
    "connection_string": f"DATABASE_URL={CONNECTION}",
    "password_assignment": 'password = "' + 'correct-horse-battery"',
}


def secret_part(label: str) -> str:
    """The part of each sample that must not survive."""
    return {
        "private_key": "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",
        "aws_access_key": AWS_KEY,
        "aws_secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "jwt": "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
        "bearer": "abcdefghijklmnop1234567890",
        "basic_auth": "dXNlcjpwYXNzd29yZDEyMzQ1Ng==",
        "github_token": GITHUB,
        "slack_token": SLACK,
        "google_api_key": GOOGLE,
        "anthropic_key": ANTHROPIC,
        "openrouter_key": OPENROUTER,
        "openai_key": OPENAI,
        "connection_string": "hunter2-hunter2",
        "password_assignment": "correct-horse-battery",
    }[label]


def test_every_builtin_pattern_has_a_sample_here() -> None:
    assert set(SHAPES) == set(BUILTIN_PATTERNS), "a new pattern needs a test, and a test needs a pattern"


CONTEXTS = {
    "alone": lambda s: s,
    "in a sentence": lambda s: f"log line: {s} (end)",
    "inside JSON text": lambda s: json.dumps({"detail": s}),
    "across lines": lambda s: f"multi\nline\n{s}\nmore",
}


@pytest.mark.parametrize("label", sorted(SHAPES))
def test_each_secret_shape_is_masked_wherever_it_appears(label: str) -> None:
    r = SecretRedactor()
    for where, wrap in CONTEXTS.items():
        masked, hits = r.redact_text(wrap(SHAPES[label]))
        assert secret_part(label) not in masked, f"{label} survived {where}"
        assert hits, f"{label} was not counted {where}"
    assert r.contains_secret(SHAPES[label])


def test_the_mask_names_what_was_hidden_so_a_reader_knows_something_was_there() -> None:
    masked, hits = SecretRedactor().redact_text(f"id {AWS_KEY}")
    assert masked == "id [REDACTED:aws_access_key]" and hits == {"aws_access_key": 1}
    kept, _ = SecretRedactor().redact_text('api_key = "' + 'abcd1234efgh"')
    assert kept == 'api_key = "[REDACTED:password_assignment]"', "the name of the setting stays, the value goes"


@pytest.mark.parametrize(
    "harmless",
    [
        "The password policy requires at least 12 characters.",
        "Reset your password at the account page.",
        "Bearer of good news: the build passed.",
        "commit 2fd4e1c67a2d28fced849ee1bb76e7391b93eb12 fixed the bug",
        "sha256: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        "https://example.com/path/to/page?x=1&y=2",
        "550e8400-e29b-41d4-a716-446655440000",
        "sk is short for 'skip' and AKIA is a prefix",
        "Ask the assistant: what is 2 + 2?",
        "AGENTLAB_CANARY_1A2B3C4D",
    ],
)
def test_ordinary_text_is_left_alone(harmless: str) -> None:
    masked, hits = SecretRedactor().redact_text(harmless)
    assert masked == harmless and not hits


def test_a_canary_is_evidence_and_is_never_masked() -> None:
    """The canaries AgentLab plants are fake values whose appearance proves a leak; hiding them would hide the finding."""
    registry = CanaryRegistry()
    canary = registry.issue("system prompt")
    masked = redact(f"The agent said: the secret is {canary}")
    assert canary in masked and registry.find(masked) == [canary]


# ============================================================================================ registered values
def test_a_registered_secret_is_masked_in_every_encoding_it_travels_in() -> None:
    r = SecretRedactor()
    secret = "s3cr3t" + "/value+with=symbols&more"
    r.register_secret(secret, label="credential:staff")
    forms = {
        "plain": secret,
        "url-quoted": urllib.parse.quote(secret, safe=""),
        "form-quoted": urllib.parse.quote_plus(secret),
        "base64": base64.b64encode(secret.encode()).decode(),
    }
    for name, form in forms.items():
        masked, hits = r.redact_text(f"value={form}")
        assert form not in masked and hits == {"credential:staff": 1}, name


def test_a_secret_that_is_too_short_to_mask_safely_is_not_registered() -> None:
    r = SecretRedactor()
    r.register_secret("abc")
    r.register_secret("")
    r.register_secret(None)
    assert r.redact_text("abc abc abc")[0] == "abc abc abc", "masking every 'abc' would destroy ordinary text"


def test_the_longest_secret_is_masked_first_so_no_fragment_of_it_remains() -> None:
    r = SecretRedactor()
    r.register_secret("tok-" + "abcdef", label="short")
    r.register_secret("tok-" + "abcdef-and-more", label="long")
    assert r.redact_text("x tok-abcdef-and-more y")[0] == "x [REDACTED:long] y"


# ================================================================================================== structures
def test_values_under_sensitive_keys_are_hidden_even_when_they_look_harmless() -> None:
    document: dict[str, Any] = {
        "Authorization": "plain-looking",
        "x-api-key": "k",
        "Cookie": "a=b",
        "set-cookie": "sid=1",
        "password": "hunter2",
        "nested": {"api_key": "v", "client_secret": "w", "list": [{"token": "t"}, {"name": "ok"}]},
        "storage_state": {"cookies": []},
        "empty": {"password": "", "token": None},
        "note": "nothing to hide",
    }
    result = SecretRedactor().redact(document)
    out = result.value
    for path in (("Authorization",), ("x-api-key",), ("Cookie",), ("set-cookie",), ("password",)):
        assert out[path[0]] == "[REDACTED:sensitive_key]"
    assert out["nested"]["api_key"] == out["nested"]["client_secret"] == "[REDACTED:sensitive_key]"
    assert out["nested"]["list"] == [{"token": "[REDACTED:sensitive_key]"}, {"name": "ok"}]
    assert out["storage_state"] == "[REDACTED:sensitive_key]"
    assert out["empty"] == {"password": "", "token": None} and out["note"] == "nothing to hide"
    assert result.status == RedactionStatus.REDACTED and result.hits["sensitive_key"] == 9
    assert SecretRedactor().redact({"a": "b"}).status == RedactionStatus.CLEAN


def test_redacting_does_not_modify_what_it_was_given() -> None:
    original = {"headers": {"Authorization": "Bearer " + "abcdefghijklmnop1234"}, "items": [f"key {AWS_KEY}"]}
    snapshot = json.dumps(original)
    SecretRedactor().redact(original)
    redact_strings(original)
    assert json.dumps(original) == snapshot


def test_a_database_record_keeps_its_field_names_and_loses_only_secret_values() -> None:
    """A tool schema may have a parameter called ``password`` and a profile a field called ``token``; hiding those *names*
    would destroy the record, while a secret *value* under any name must never be written."""
    schema = {"name": "login", "parameters": {"password": {"type": "string"}, "token": {"type": "string"}}}
    assert redact_strings(schema) == schema
    leaked = {"output": f"found {AWS_KEY}", "nested": [{"headers": ("a", f"Bearer {'abcdefghijklmnop1234'}")}]}
    out = redact_strings(leaked)
    assert AWS_KEY not in json.dumps(out) and "abcdefghijklmnop1234" not in json.dumps(out)
    assert out["nested"][0]["headers"][0] == "a", "structure and ordinary values survive"
    assert redact_strings(42) == 42 and redact_strings(None) is None


# ================================================================================================ the sinks
Base = declarative_base()


class Row(Base):  # type: ignore[misc, valid-type]
    __tablename__ = "rows"
    __test__ = False
    id = Column(Integer, primary_key=True)
    payload = Column(RedactedJSON)
    reason = Column(RedactedText)
    plain = Column(JSON)
    note = Column(Text)


def test_database_columns_scrub_secrets_on_the_way_in_and_the_file_never_holds_them(tmp_path: Path) -> None:
    db = tmp_path / "rows.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        session.add(
            Row(
                id=1,
                payload={
                    "output": f"the key is {AWS_KEY}",
                    "deep": [{"url": CONNECTION}],
                    "password": {"type": "string"},
                },
                reason=f"credential rejected: Bearer {'abcdefghijklmnop1234567890'}",
                plain={"output": "unprotected"},
            )
        )
        session.commit()
        row = session.get(Row, 1)
        assert row is not None
        assert AWS_KEY not in json.dumps(row.payload) and "hunter2-hunter2" not in json.dumps(row.payload)
        assert row.payload["password"] == {"type": "string"}, "a field *name* is not a secret"
        assert "abcdefghijklmnop1234567890" not in row.reason and "[REDACTED:bearer]" in row.reason
        assert row.plain == {"output": "unprotected"}
    engine.dispose()
    raw = db.read_bytes()
    for secret in (AWS_KEY, "hunter2-hunter2", "abcdefghijklmnop1234567890"):
        assert secret.encode() not in raw, f"{secret[:6]}... is in the database file"
    conn = sqlite3.connect(db)
    try:
        assert AWS_KEY not in str(conn.execute(text("select payload from rows").compile().string).fetchall())
    finally:
        conn.close()


def test_a_log_record_is_masked_in_its_message_its_arguments_and_its_traceback() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingLogFilter())
    logger = logging.getLogger("agentlab.test.redaction")
    logger.handlers, logger.propagate = [handler], False
    logger.setLevel(logging.DEBUG)
    try:
        logger.info("calling with %s and key=%s", f"Bearer {'abcdefghijklmnop1234567890'}", AWS_KEY)
        try:
            raise RuntimeError(f"upstream said: {GITHUB}")
        except RuntimeError:
            logger.exception("failed")
    finally:
        logger.handlers.clear()
    written = stream.getvalue()
    for secret in ("abcdefghijklmnop1234567890", AWS_KEY, GITHUB):
        assert secret not in written
    assert "calling with" in written and "RuntimeError" in written, "the log is still useful"


def test_a_filter_that_cannot_redact_withholds_the_record_instead_of_logging_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agentlab.security.redactor as module

    def broken(_value: Any) -> Any:
        raise RuntimeError("redactor failure")

    monkeypatch.setattr(module, "redact", broken)
    record = logging.LogRecord("x", logging.INFO, __file__, 1, f"secret {AWS_KEY}", None, None)
    assert RedactingLogFilter().filter(record) is True
    assert AWS_KEY not in record.getMessage() and "withheld" in record.getMessage()


def test_custom_patterns_from_the_configuration_extend_the_built_in_ones() -> None:
    r = SecretRedactor(extra_patterns=[r"ACME-[0-9]{6}"])
    assert (
        r.redact_text("ticket ACME-123456 and " + AWS_KEY)[0]
        == "ticket [REDACTED:custom_0] and [REDACTED:aws_access_key]"
    )
