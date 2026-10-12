"""DiagnosticRedactor: what it masks, what it keeps so a failure can still be understood, and what it reports.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import json
import time

import pytest

from agentlab.security.diagnostic_redactor import DiagnosticRedactor
from agentlab.security.redactor import SecretRedactor

OPENAI = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"
GITHUB = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"
JWT = (
    "eyJ"
    + "hbGciOiJIUzI1NiJ9"
    + "."
    + "eyJ"
    + "zdWIiOiIxMjM0NTY3ODkwIn0"
    + "."
    + "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
)
PASSWORD = "S3cret" + "Passw0rd" + "!x"
OPAQUE = "q9XvK2mLpR7tYw3bNc8Zf5HjD1sGa6Ue"  # 32 characters, mixed case and digits: looks like a key
KEY_BLOCK = (
    "-----BEGIN " + "RSA PRIVATE" + " KEY-----\n"
    "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n"
    "KUpRKfFLfRYC9AIKjbJTWit+CqzLT64R7pM=\n"
    "-----END " + "RSA PRIVATE" + " KEY-----"
)


@pytest.fixture
def redactor() -> DiagnosticRedactor:
    return DiagnosticRedactor(base=SecretRedactor())


def kinds(result) -> set[str]:  # type: ignore[no-untyped-def]
    return {f.kind for f in result.findings}


# ================================================================================================== what it keeps
def test_an_authorization_header_keeps_its_name_and_scheme_so_the_failure_can_still_be_read(
    redactor: DiagnosticRedactor,
) -> None:
    text = (
        f"POST /v1/chat HTTP/1.1\nAuthorization: Bearer {OPENAI}\nContent-Type: application/json\nX-Request-Id: r-42\n"
    )
    result = redactor.redact_text(text)
    assert "Authorization: Bearer [REDACTED:authorization]" in result.text
    assert "POST /v1/chat HTTP/1.1" in result.text and "X-Request-Id: r-42" in result.text
    assert OPENAI not in result.text and "credential_header" in kinds(result)
    basic = redactor.redact_text("Authorization: Basic dXNlcjpwYXNzd29yZDEyMzQ=\n")
    assert basic.text.startswith("Authorization: Basic [REDACTED:authorization]")


def test_cookies_keep_their_names_and_lose_their_values(redactor: DiagnosticRedactor) -> None:
    text = "Cookie: session=abc123def456ghi789; theme=dark; csrftoken=zzz999yyy888\nSet-Cookie: sid=0a1b2c3d4e5f; Path=/; HttpOnly\n"
    result = redactor.redact_text(text)
    assert "session=[REDACTED:cookie]" in result.text and "theme=[REDACTED:cookie]" in result.text
    assert "abc123def456ghi789" not in result.text and "0a1b2c3d4e5f" not in result.text
    assert "HttpOnly" in result.text and "cookie" in kinds(result)


def test_a_connection_string_keeps_its_scheme_user_and_host(redactor: DiagnosticRedactor) -> None:
    text = f"could not connect to postgresql://agentlab:{PASSWORD}@db.internal:5432/agentlab (timeout)"
    result = redactor.redact_text(text)
    assert PASSWORD not in result.text
    assert "postgresql://agentlab:" in result.text and "@db.internal:5432/agentlab (timeout)" in result.text


def test_assignments_and_query_strings_keep_the_name_and_mask_the_value(redactor: DiagnosticRedactor) -> None:
    text = (
        f'export OPENAI_API_KEY="{OPENAI}"\nAGENTLAB_DB_PASSWORD={PASSWORD}\nDEBUG=true\n'
        f"GET /callback?code=1&access_token={GITHUB}&page=2 HTTP/1.1\n"
    )
    result = redactor.redact_text(text)
    assert OPENAI not in result.text and PASSWORD not in result.text and GITHUB not in result.text
    assert "OPENAI_API_KEY=" in result.text and "AGENTLAB_DB_PASSWORD=" in result.text
    assert "DEBUG=true" in result.text and "page=2" in result.text and "code=1" in result.text
    assert {"secret_assignment", "query_secret"} <= kinds(result)


def test_known_secret_shapes_are_masked_wherever_they_are(redactor: DiagnosticRedactor) -> None:
    text = f"token {JWT} and {AWS} and a key {GITHUB}\n{KEY_BLOCK}\nthe end"
    result = redactor.redact_text(text)
    for secret in (JWT, AWS, GITHUB, "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p"):
        assert secret not in result.text
    assert "the end" in result.text
    assert {"jwt", "aws_access_key", "private_key"} <= kinds(result)
    key_finding = next(f for f in result.findings if f.kind == "private_key")
    assert key_finding.location == "line 2", "a block that spans lines is located by where it starts"


def test_a_value_the_server_registered_is_masked_in_free_text() -> None:
    base = SecretRedactor()
    base.register_secret("hunter2-but-longer-1234", "provider_key")
    result = DiagnosticRedactor(base=base).redact_text(
        "the provider replied: invalid key hunter2-but-longer-1234, retry"
    )
    assert "hunter2-but-longer-1234" not in result.text and "provider_key" in kinds(result)


# ============================================================================================ personal information
def test_personal_information_is_masked_and_look_alikes_are_not(redactor: DiagnosticRedactor) -> None:
    text = (
        "contact jane.doe@example.com or +44 20 7946 0958 or (415) 555-2671\n"
        "cloned git@github.com:org/repo.git at 2026-10-12 14:33:20 from 192.168.10.20 build v1.2.3+4 id 1697000000000\n"
        "card 4111 1111 1111 1111 and order 1234 5678 9012 3456\n"
    )
    result = redactor.redact_text(text)
    for private in ("jane.doe@example.com", "20 7946 0958", "555-2671", "4111 1111 1111 1111"):
        assert private not in result.text, private
    for kept in ("git@github.com:org/repo.git", "2026-10-12 14:33:20", "192.168.10.20", "v1.2.3+4", "1697000000000"):
        assert kept in result.text, kept
    assert "1234 5678 9012 3456" in result.text, "a number that fails the card checksum is not a card"
    assert {"email", "phone", "card_number"} <= kinds(result)
    assert next(f for f in result.findings if f.kind == "card_number").count == 1


def test_ip_addresses_are_kept_unless_the_owner_asks_for_them_to_go() -> None:
    text = "request from 203.0.113.9 to 10.0.0.5"
    assert "203.0.113.9" in DiagnosticRedactor(base=SecretRedactor()).redact_text(text).text
    masked = DiagnosticRedactor(base=SecretRedactor(), redact_ip_addresses=True).redact_text(text)
    assert "203.0.113.9" not in masked.text and "10.0.0.5" not in masked.text and "ip_address" in kinds(masked)


def test_the_owners_own_rules_apply() -> None:
    redactor = DiagnosticRedactor(base=SecretRedactor(), custom_rules=[("employee_id", r"\bEMP-\d{6}\b")])
    result = redactor.redact_text("approved by EMP-482913 on Tuesday")
    assert result.text == "approved by [REDACTED:employee_id] on Tuesday" and "custom:employee_id" in kinds(result)
    assert "custom rules" in result.mechanisms


# ============================================================================================== structured input
def test_json_is_masked_by_key_and_the_location_is_the_path(redactor: DiagnosticRedactor) -> None:
    doc = {
        "request": {
            "headers": {"Authorization": f"Bearer {OPENAI}", "X-Request-Id": "r-1", "Cookie": "sid=abc123456"},
            "body": {"password": PASSWORD, "city": "Berlin", "note": f"mail me at jane@example.com about {GITHUB}"},
        },
        "attempts": [{"api_key": AWS}, {"ok": True}],
    }
    result = redactor.redact_text(json.dumps(doc))
    masked = json.loads(result.text)
    assert masked["request"]["headers"]["Authorization"] == "[REDACTED:sensitive_key]"
    assert masked["request"]["headers"]["X-Request-Id"] == "r-1" and masked["request"]["body"]["city"] == "Berlin"
    assert "jane@example.com" not in result.text and GITHUB not in result.text and PASSWORD not in result.text
    assert masked["attempts"][1] == {"ok": True}
    locations = {f.location for f in result.findings}
    assert "$.request.headers.Authorization" in locations and "$.attempts[0].api_key" in locations
    assert "structured fields" in result.mechanisms


def test_json_lines_are_masked_line_by_line_and_a_line_that_is_not_json_is_still_text(
    redactor: DiagnosticRedactor,
) -> None:
    text = "\n".join(
        [
            json.dumps({"level": "info", "msg": "start"}),
            json.dumps({"level": "error", "token": OPENAI, "msg": "failed for jane@example.com"}),
            f"plain line with {GITHUB}",
        ]
    )
    result = redactor.redact_text(text)
    lines = result.text.split("\n")
    assert json.loads(lines[0]) == {"level": "info", "msg": "start"}
    assert json.loads(lines[1])["token"] == "[REDACTED:sensitive_key]"
    assert OPENAI not in result.text and GITHUB not in result.text and "jane@example.com" not in result.text
    assert any(f.location == "line 3" for f in result.findings)


# ===================================================================================== the second inspection
def test_a_token_that_looks_like_a_key_is_masked_and_the_text_needs_review(redactor: DiagnosticRedactor) -> None:
    result = redactor.redact_text(f"the service said: session id {OPAQUE} is invalid\nrequest finished in 0.2s\n")
    assert OPAQUE not in result.text and "[REDACTED:high_entropy]" in result.text
    assert result.status == "needs_review" and result.external_sharing_blocked is True
    assert result.residual and result.residual[0].kind == "high_entropy_token"


def test_identifiers_and_paths_that_logs_are_full_of_do_not_trigger_a_review(redactor: DiagnosticRedactor) -> None:
    text = (
        "trace 7f9c2a4e-1b3d-4c5e-8a6f-0123456789ab span 4bf92f3577b34da6a3ce929d0e0e4736\n"
        "commit 2c26b46b68ffc68ff99b453c1d30413413422d706 sha256 "
        "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae\n"
        "opened /var/lib/docker/volumes/agentlab_data/_data/artifacts/restricted/ab at pod agentlab-api-7d9f8b6c5-xk2lp\n"
    )
    result = redactor.redact_text(text)
    assert result.text == text and result.status == "clean" and not result.external_sharing_blocked


def test_masking_twice_changes_nothing_and_the_report_holds_no_values(redactor: DiagnosticRedactor) -> None:
    text = f"Authorization: Bearer {OPENAI}\nmail jane@example.com password={PASSWORD}\n{KEY_BLOCK}\n"
    once = redactor.redact_text(text)
    again = redactor.redact_text(once.text)
    assert again.text == once.text and again.status == "clean"
    report = json.dumps(once.report())
    for secret in (OPENAI, "jane@example.com", PASSWORD, "MIIBOgIBAAJBAKj34"):
        assert secret not in report
    assert once.report()["status"] == "redacted" and once.report()["counts"]["email"] == 1
    assert "does not guarantee" in once.report()["note"]


def test_a_status_says_what_happened(redactor: DiagnosticRedactor) -> None:
    assert redactor.redact_text("nothing sensitive here, just a stack trace at line 42").status == "clean"
    assert redactor.redact_text("mail jane@example.com").status == "redacted"
    assert redactor.redact_text("session id " + OPAQUE).status == "needs_review"
    assert redactor.inspect("session id " + OPAQUE) and not redactor.inspect("hello")


def test_a_large_log_is_masked_in_a_reasonable_time(redactor: DiagnosticRedactor) -> None:
    line = "2026-10-12T10:00:00Z INFO request id=r-%d path=/v1/chat status=200 took=12ms user=jane@example.com\n"
    text = "".join(line % i for i in range(20_000))  # about 2 MB
    started = time.perf_counter()
    result = redactor.redact_text(text)
    elapsed = time.perf_counter() - started
    assert "jane@example.com" not in result.text and result.counts()["email"] == 20_000
    assert elapsed < 20, f"masking 2 MB took {elapsed:.1f}s"


# ================================================================================ hostile input, in bounded time
ADVERSARIAL = {
    "one long word": "x" * 60_000,
    "dots": "a." * 30_000,
    "dashes": "a-" * 30_000,
    "slashes": "a/" * 30_000,
    "digits": "1" * 60_000,
    "digits and spaces": "1 " * 30_000,
    "spaces": " " * 60_000,
    "equals": "=" * 60_000,
    "colons": ":" * 60_000,
    "at signs": "@" * 60_000,
    "mail-like": "a@a." * 15_000,
    "schemes": "a://" * 15_000,
    "key markers": ("-----BEGIN " + "PRIVATE" + " KEY-----") * 4_000,
    "jwt starts": "eyJ" * 20_000,
    "bearers": "Bearer " * 8_000,
    "long bearer": "Bearer " + "A" * 60_000,
    "assignments": "password=" * 6_000,
    "long assignment": "password=" + "A" * 60_000,
    "markers": "[REDACTED:x]" * 5_000,
    "names and equals": "a=" * 30_000,
    "headers": "Authorization: " * 4_000,
    "cookies": "Cookie: " + "a=b; " * 12_000,
    "quotes": '"' * 60_000,
    "backslashes": "\\" * 60_000,
    "newlines": "\n" * 60_000,
}


def test_no_pattern_is_quadratic() -> None:
    """The text a person attaches is hostile, up to megabytes: every pattern, the existing redactor's included, must read
    it in time proportional to its length. A pattern that can start at every position of a long run re-reads the run from
    each (a lazy [\\s\\S]+? looking for an END that is not there is the classic). 60,000 characters would take a quadratic
    pattern half a minute; a linear one takes milliseconds."""
    import re

    from agentlab.security import diagnostic_redactor as module
    from agentlab.security.redactor import BUILTIN_PATTERNS

    patterns = {
        f"diagnostic{name}": value
        for name, value in vars(module).items()
        if name.startswith("_") and isinstance(value, re.Pattern)
    }
    patterns.update({f"base:{name}": re.compile(src) for name, src in BUILTIN_PATTERNS.items()})
    assert len(patterns) > 20
    slow = []
    for pname, pattern in patterns.items():
        for iname, text in ADVERSARIAL.items():
            started = time.perf_counter()
            pattern.sub("", text)
            took = time.perf_counter() - started
            if took > 1.0:
                slow.append(f"{pname} on {iname}: {took:.1f}s")
    assert not slow, "patterns that are not linear: " + "; ".join(slow)


def test_the_whole_redactor_reads_hostile_text_in_bounded_time(redactor: DiagnosticRedactor) -> None:
    for name, text in ADVERSARIAL.items():
        started = time.perf_counter()
        result = redactor.redact_text(text)
        took = time.perf_counter() - started
        assert took < 8, f"{name}: {took:.1f}s"
        assert result.status in {"clean", "redacted", "needs_review"}


def test_private_key_blocks_are_masked_in_linear_time_whatever_their_shape() -> None:
    from agentlab.security.diagnostic_redactor import mask_private_keys

    begin, end = "-----BEGIN " + "RSA PRIVATE" + " KEY-----", "-----END " + "RSA PRIVATE" + " KEY-----"
    text = f"a\n{begin}\nAAAA\nBBBB\n{end}\nmid\n{begin}\nCCCC\n{end}\nz"
    out, lines = mask_private_keys(text)
    assert lines == [2, 7] and "AAAA" not in out and "CCCC" not in out and out.startswith("a\n[REDACTED:private_key]")
    assert out.count("\n") == text.count("\n"), "the masked block keeps its lines, so later line numbers match the file"
    assert out.endswith("\nz") and out.count("[REDACTED:private_key]") == 2

    truncated, _ = mask_private_keys(f"{begin}\nAAAA\nBBBB\n\nafter the blank line")
    assert "AAAA" not in truncated and "after the blank line" in truncated, "an unterminated block is masked to its end"
    nested, found = mask_private_keys(f"{begin}\nFIRST\n{begin}\nSECOND\n{end}\nfin")
    assert "FIRST" not in nested and "SECOND" not in nested and found == [1, 3] and nested.endswith("fin")

    started = time.perf_counter()
    swamp, many = mask_private_keys((begin + "\n") * 30_000)
    assert time.perf_counter() - started < 2 and len(many) == 30_000 and "RSA" not in swamp
