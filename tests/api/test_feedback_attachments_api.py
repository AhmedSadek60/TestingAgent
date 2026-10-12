"""Attachments over HTTP: uploading, what is refused and why, who may see a file, and how a download is sent.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any

import httpx

from agentlab.api.routes.feedback import _content_disposition
from agentlab.core.config import AttachmentsConfig
from tests.api.test_feedback_api import ISSUE, feedback_server, maintainer, reporter

OPENAI = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 300
LOG = f"10:00:01 INFO start\nAuthorization: Bearer {OPENAI}\n10:00:02 ERROR call failed for jane@example.com\n".encode()


def zipped(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


async def file_issue(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    r = await client.post("/submissions", json=ISSUE, headers=headers)
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


async def upload(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    sid: str,
    name: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    **form: str,
) -> httpx.Response:
    return await client.post(
        f"/submissions/{sid}/attachments", files={"file": (name, data, content_type)}, data=form, headers=headers
    )


# ============================================================================================== the happy path
async def test_a_reporter_attaches_a_log_and_a_screenshot_and_follows_them(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        made = await file_issue(alice, ah)
        sid = made["id"]

        log = await upload(alice, ah, sid, "run.log", LOG, "text/plain")
        assert log.status_code == 201, log.text
        body = log.json()
        assert body["name"] == "run.log" and body["type"] == "text/plain" and body["mine"] is True
        assert body["secrets"]["status"] == "redacted" and body["secrets"]["counts"]["email"] == 1
        assert body["scan"]["status"] == "not_scanned" and body["sanitized_available"] is True
        assert OPENAI not in log.text and "jane@example.com" not in log.text, "the answer holds no value"

        shot = await upload(alice, ah, sid, "screen.png", PNG, "image/png")
        assert shot.status_code == 201 and shot.json()["secrets"]["status"] == "not_applicable"

        listing = (await alice.get(f"/submissions/{sid}/attachments")).json()
        assert [a["name"] for a in listing] == ["run.log", "screen.png"]
        one = (await alice.get(f"/submissions/{sid}/attachments/{body['id']}")).json()
        assert one["sha256"] == body["sha256"] and len(one["sha256"]) == 64

        original = await alice.get(f"/submissions/{sid}/attachments/{body['id']}/download")
        assert original.status_code == 200 and original.content == LOG
        sanitized = await alice.get(
            f"/submissions/{sid}/attachments/{body['id']}/download", params={"version": "sanitized"}
        )
        assert OPENAI.encode() not in sanitized.content and b"jane@example.com" not in sanitized.content
        assert b"ERROR call failed" in sanitized.content, "what is not secret is kept"

        preview = (await alice.get(f"/submissions/{sid}/attachments/{body['id']}/preview")).json()
        assert preview["kind"] == "text" and OPENAI not in preview["text"] and preview["sanitized"] is True
        assert (await alice.get(f"/submissions/{sid}/attachments/{shot.json()['id']}/preview")).json()[
            "kind"
        ] == "image"

        gone = await alice.delete(f"/submissions/{sid}/attachments/{body['id']}", headers=ah)
        assert gone.status_code == 204
        assert (await alice.get(f"/submissions/{sid}/attachments/{body['id']}/download")).status_code == 404
        assert [a["name"] for a in (await alice.get(f"/submissions/{sid}/attachments")).json()] == ["screen.png"]
        await alice.aclose()


async def test_a_download_is_sent_as_a_download_of_unknown_type_and_cannot_break_out_of_its_header(
    tmp_path: Path,
) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        sid = (await file_issue(alice, ah))["id"]
        sneaky = "invoice\r\nSet-Cookie: pwned=1\r\n.txt"
        made = await upload(alice, ah, sid, sneaky, b"hello", "text/html")
        assert made.status_code == 201, made.text
        r = await alice.get(f"/submissions/{sid}/attachments/{made.json()['id']}/download")
        disposition = r.headers["content-disposition"]
        assert disposition.startswith("attachment;") and "\r" not in disposition and "\n" not in disposition
        assert "pwned" not in r.headers.get("set-cookie", "") and "set-cookie" not in r.headers
        assert r.headers["content-type"] == "application/octet-stream", "never the type the uploader claimed"
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
        assert "sandbox" in r.headers["content-security-policy"]

        unicode_name = await upload(alice, ah, sid, "résumé — v2.txt", b"hello again")
        d = (await alice.get(f"/submissions/{sid}/attachments/{unicode_name.json()['id']}/download")).headers
        assert "filename*=UTF-8''r%C3%A9sum%C3%A9%20%E2%80%94%20v2.txt" in d["content-disposition"]
        assert re.search(r'filename="[A-Za-z0-9._-]+"', d["content-disposition"]), "an ASCII fallback is present"
        await alice.aclose()


# ============================================================================================ what is refused
async def test_a_file_that_is_not_what_it_says_is_refused_with_the_reason(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        sid = (await file_issue(alice, ah))["id"]
        cases = [
            ("photo.png", b"MZ\x90\x00 this is a program", "image/png", 422, "not a PNG"),
            ("report.pdf", b"<html><script>alert(1)</script></html>", "application/pdf", 422, "not a PDF"),
            ("notes.txt", PNG, "text/plain", 422, "binary data|not UTF-8"),
            ("dump.zip", b"not an archive", "application/zip", 422, "not a ZIP"),
            ("empty.txt", b"", "text/plain", 422, "empty"),
            ("setup.exe", b"MZ", "application/octet-stream", 403, "cannot be attached"),
            ("page.html", b"<html></html>", "text/html", 403, "cannot be attached"),
            ("vector.svg", b"<svg onload=alert(1)/>", "image/svg+xml", 403, "cannot be attached"),
            ("noextension", b"hello", "text/plain", 403, "cannot be attached"),
        ]
        for name, data, ctype, status, message in cases:
            r = await upload(alice, ah, sid, name, data, ctype)
            assert r.status_code == status, (name, r.status_code, r.text[:200])
            assert re.search(message, r.json()["error"]["message"]), (name, r.json())
        assert (await alice.get(f"/submissions/{sid}/attachments")).json() == [], "nothing was kept"
        await alice.aclose()


async def test_dangerous_archives_are_refused_before_anything_is_kept(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        sid = (await file_issue(alice, ah))["id"]
        traversal = zipped({"ok.log": b"fine", "../../etc/cron.d/job": b"* * * * * root id"})
        r = await upload(alice, ah, sid, "traversal.zip", traversal)
        assert r.status_code == 403 and "climbs out" in r.json()["error"]["message"]
        program = zipped({"logs/a.log": b"fine", "run.sh": b"#!/bin/sh\nrm -rf /"})
        r = await upload(alice, ah, sid, "program.zip", program)
        assert r.status_code == 403 and "program or script" in r.json()["error"]["message"]
        bomb = zipped({"zeros.log": b"\x00" * (60 * 1024 * 1024)})
        r = await upload(alice, ah, sid, "bomb.zip", bomb)
        assert r.status_code == 403 and "bomb" in r.json()["error"]["message"]
        assert (await alice.get(f"/submissions/{sid}/attachments")).json() == []
        await alice.aclose()


async def test_a_file_over_the_limit_is_refused_with_413(tmp_path: Path) -> None:
    async with feedback_server(tmp_path, attachments=AttachmentsConfig(max_file_mb=1, max_total_mb=5)) as api:
        alice, ah = await reporter(api)
        sid = (await file_issue(alice, ah))["id"]
        r = await upload(alice, ah, sid, "big.log", b"INFO fine\n" * 150_000, "text/plain")  # 1.5 MB
        assert r.status_code == 413 and r.json()["error"]["kind"] == "too_large"
        assert "feedback.attachments.max_file_mb" in r.json()["error"]["message"]
        assert (await upload(alice, ah, sid, "ok.log", b"INFO fine\n" * 1000, "text/plain")).status_code == 201
        await alice.aclose()


async def test_the_number_of_files_is_limited_and_the_limit_is_not_a_surprise(tmp_path: Path) -> None:
    async with feedback_server(tmp_path, attachments=AttachmentsConfig(max_files=2)) as api:
        alice, ah = await reporter(api)
        sid = (await file_issue(alice, ah))["id"]
        for n in range(2):
            assert (await upload(alice, ah, sid, f"{n}.txt", f"file {n}\n".encode())).status_code == 201
        r = await upload(alice, ah, sid, "2.txt", b"file 2\n")
        assert r.status_code == 403 and "already has 2 attachments" in r.json()["error"]["message"]
        await alice.aclose()


async def test_a_browser_session_needs_the_csrf_token_to_attach_or_remove(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        sid = (await file_issue(alice, ah))["id"]
        assert (await upload(alice, {}, sid, "a.txt", b"hello")).status_code == 403
        made = await upload(alice, ah, sid, "a.txt", b"hello")
        assert made.status_code == 201
        assert (await alice.delete(f"/submissions/{sid}/attachments/{made.json()['id']}")).status_code == 403
        await alice.aclose()


# ================================================================================================ who may see
async def test_another_reporter_is_told_nothing_about_an_attachment_and_cannot_use_it(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api, "alice")
        bob, bh = await reporter(api, "bob")
        sid = (await file_issue(alice, ah))["id"]
        att = (await upload(alice, ah, sid, "run.log", LOG)).json()
        ghost = "00000000-0000-4000-8000-000000000000"
        base = f"/submissions/{sid}/attachments"
        for method, path, kwargs in (
            ("GET", base, {}),
            ("GET", f"{base}/{att['id']}", {}),
            ("GET", f"{base}/{att['id']}/download", {}),
            ("GET", f"{base}/{att['id']}/preview", {}),
            ("DELETE", f"{base}/{att['id']}", {"headers": bh}),
            ("POST", base, {"files": {"file": ("x.txt", b"hello", "text/plain")}, "headers": bh}),
        ):
            real = await bob.request(method, path, **kwargs)
            missing = await bob.request(method, path.replace(sid, ghost), **kwargs)
            assert real.status_code == missing.status_code == 404, (method, path, real.status_code)
            assert real.json() == missing.json(), "hidden and missing are told apart by nothing"
        await alice.aclose()
        await bob.aclose()


async def test_a_maintainers_internal_file_is_invisible_to_the_reporter_and_a_reporter_cannot_make_one(
    tmp_path: Path,
) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        maria, mh = await maintainer(api)
        sid = (await file_issue(alice, ah))["id"]
        refused = await upload(alice, ah, sid, "mine.txt", b"x", visibility="internal")
        assert refused.status_code == 403 and "only maintainers" in refused.json()["error"]["message"]
        note = await upload(maria, mh, sid, "notes.txt", b"customer is on a trial\n", visibility="internal")
        assert note.status_code == 201 and note.json()["visibility"] == "internal"
        assert (await alice.get(f"/submissions/{sid}/attachments")).json() == []
        assert (await alice.get(f"/submissions/{sid}/attachments/{note.json()['id']}")).status_code == 404
        assert [a["name"] for a in (await maria.get(f"/submissions/{sid}/attachments")).json()] == ["notes.txt"]
        for c in (alice, maria):
            await c.aclose()


async def test_downloads_are_recorded_for_the_audit_trail(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        sid = (await file_issue(alice, ah))["id"]
        att = (await upload(alice, ah, sid, "run.log", LOG)).json()
        await api.client.get(f"/submissions/{sid}/attachments/{att['id']}/download")
        trail = (await api.client.get(f"/submissions/{sid}/audit")).json()
        events = [(e["event"], e["actor"]) for e in trail]
        assert ("attachment_added", "alice") in events and ("attachment_downloaded", "shared API token") in events
        await alice.aclose()


async def test_the_api_description_lists_the_attachment_operations(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        doc = api.app.openapi()
        found = {(m.upper(), p) for p, methods in doc["paths"].items() if "/attachments" in p for m in methods}
        assert len(found) == 6, sorted(found)
        assert doc["paths"]["/submissions/{submission_id}/attachments"]["post"]["requestBody"]["content"].get(
            "multipart/form-data"
        )


def test_the_download_header_cannot_be_broken_out_of_whatever_the_stored_name_holds() -> None:
    for name in ('a"; filename="evil.exe', "x\r\nSet-Cookie: pwned=1\r\n", "  ", "../../etc/passwd", "ünï.txt", ""):
        header = _content_disposition(name)
        assert header.startswith('attachment; filename="') and "\r" not in header and "\n" not in header
        assert header.count('"') == 2, header
        assert re.fullmatch(r"attachment; filename=\"[A-Za-z0-9._-]+\"; filename\*=UTF-8''[A-Za-z0-9%._~-]*", header), (
            header
        )
