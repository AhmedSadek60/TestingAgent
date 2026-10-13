"""Attachments, attacked: files that are not what they say, hostile archives and names, other people's files, secrets that
must not travel, and the time a hostile file may take.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import httpx

from tests.api.test_accounts_api import PASSWORD, accounts, csrf, sign_in
from tests.api.test_feedback_api import ISSUE, feedback_server, maintainer, reporter
from tests.security.test_accounts_security import REPORTER_MAY, operations
from tests.security.test_feedback_security import STAFF_ONLY
from tests.support.api import product_logging

OPENAI = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
ATTACHMENT_OPERATIONS = {
    ("POST", "/submissions/{submission_id}/attachments"),
    ("GET", "/submissions/{submission_id}/attachments"),
    ("GET", "/submissions/{submission_id}/attachments/{attachment_id}"),
    ("GET", "/submissions/{submission_id}/attachments/{attachment_id}/preview"),
    ("GET", "/submissions/{submission_id}/attachments/{attachment_id}/download"),
    ("DELETE", "/submissions/{submission_id}/attachments/{attachment_id}"),
}


async def made(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    r = await client.post("/submissions", json=ISSUE, headers=headers)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def put(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    sid: str,
    name: str,
    data: bytes,
    ctype: str = "text/plain",
    **form: str,
) -> httpx.Response:
    return await client.post(
        f"/submissions/{sid}/attachments", files={"file": (name, data, ctype)}, data=form, headers=headers
    )


# ===================================================================================== who may call what
async def test_the_attachment_operations_are_classified_and_the_ones_a_reporter_may_use_are_for_their_own_only(
    tmp_path: Path,
) -> None:
    async with feedback_server(tmp_path) as api:
        found = {(m, p) for m, p in operations(api) if "/attachments" in p}
        assert found == ATTACHMENT_OPERATIONS
        assert ATTACHMENT_OPERATIONS <= REPORTER_MAY, "a reporter may attach to their own submission"
        assert not ATTACHMENT_OPERATIONS & STAFF_ONLY
        # and an anonymous caller reaches none of them
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
        ) as anonymous:
            for method, path in sorted(found):
                url = path.replace("{submission_id}", "x1x1x1").replace("{attachment_id}", "x1x1x1x1")
                r = await anonymous.request(method, url)
                assert r.status_code == 401, (method, path, r.status_code)


# ==================================================================================== files and names
async def test_a_file_cannot_pass_as_something_else_by_its_name_or_its_declared_type(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        sid = await made(alice, ah)
        disguises = [
            ("holiday.png", b"MZ\x90\x00\x03\x00\x00\x00", "image/png"),  # a Windows program named like a picture
            ("invoice.pdf", b"#!/bin/sh\nrm -rf /\n", "application/pdf"),
            (
                "report.docx",
                b"<?php system($_GET['c']); ?>",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            ("notes.txt", b"\xff\xfe<\x00s\x00c\x00r\x00i\x00p\x00t\x00>\x00", "text/plain"),
            ("data.json", b"\x00\x00\x00\x00", "application/json"),
            ("logs.zip", b"GIF89a\x01\x00\x01\x00", "application/zip"),
        ]
        for name, data, ctype in disguises:
            r = await put(alice, ah, sid, name, data, ctype)
            assert r.status_code == 422, (name, r.status_code, r.text[:160])
        for name in ("run.exe", "setup.msi", "page.html", "x.svg", "x.php", "x.jsp", "x.sh", "x.bat", "x.dll", "x.jar"):
            r = await put(alice, ah, sid, name, b"content", "text/plain")
            assert r.status_code == 403, (name, r.status_code)
        double = await put(alice, ah, sid, "photo.png.exe", PNG, "image/png")
        assert double.status_code == 403, "the last extension decides"
        assert (await alice.get(f"/submissions/{sid}/attachments")).json() == []
        await alice.aclose()


async def test_a_hostile_name_is_data_and_never_a_path_a_header_or_markup(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        sid = await made(alice, ah)
        for raw in (
            "../../../etc/passwd.txt",
            "..\\..\\windows\\system32\\config\\sam.txt",
            "<script>alert(document.cookie)</script>.txt",
            "a\u202etxt.exe.txt",
            "nul\x00.txt",
            "report'; DROP TABLE submission_attachments; --.txt",
            "x" * 400 + ".txt",
        ):
            r = await put(alice, ah, sid, raw, f"content of {raw[:10]!r}\n".encode())
            assert r.status_code == 201, (raw[:30], r.status_code, r.text[:160])
            body = r.json()
            assert (
                "/" not in body["name"] and "\\" not in body["name"] and ".." not in body["name"].split(".txt")[0][:2]
            )
            assert "\u202e" not in body["name"] and "\x00" not in body["name"] and len(body["name"]) <= 120
            assert r.headers["content-type"].startswith("application/json")
            d = await alice.get(f"/submissions/{sid}/attachments/{body['id']}/download")
            assert d.status_code == 200 and "\n" not in d.headers["content-disposition"]
        listing = (await alice.get(f"/submissions/{sid}/attachments")).json()
        assert len(listing) == 7
        await alice.aclose()


# ======================================================================================= archives
def zipped(entries: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            z.writestr(info, "/etc/passwd")
    return buf.getvalue()


async def test_hostile_archives_are_refused_in_every_form_and_in_bounded_time(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        sid = await made(alice, ah)
        hostile = {
            "traversal": zipped({"../../etc/passwd": b"x"}),
            "absolute": zipped({"/etc/cron.d/x": b"x"}),
            "windows path": zipped({"C:\\Windows\\x.txt": b"x"}),
            "symlink": zipped({"a.log": b"fine"}, symlink="link"),
            "program": zipped({"a.log": b"fine", "update.exe": b"MZ"}),
            "nested": zipped({"inner.zip": zipped({"a.log": b"x"})}),
            "many entries": zipped({f"f{i}.log": b"x" for i in range(400)}),
            "bomb": zipped({"zeros.log": b"\x00" * (80 * 1024 * 1024)}),
            "huge declared": zipped({f"p{i}.log": b"\x00" * (30 * 1024 * 1024) for i in range(8)}),
        }
        for name, data in hostile.items():
            started = time.perf_counter()
            r = await put(alice, ah, sid, f"{name.replace(' ', '-')}.zip", data, "application/zip")
            took = time.perf_counter() - started
            assert r.status_code in (403, 413), (name, r.status_code, r.text[:200])
            assert took < 15, f"{name} took {took:.1f}s"
        assert (await alice.get(f"/submissions/{sid}/attachments")).json() == []
        await alice.aclose()


async def test_a_hostile_text_file_cannot_hold_the_server_for_long(tmp_path: Path) -> None:
    """Text is searched for secrets by patterns that must read it in time proportional to its length."""
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        maria, mh = await maintainer(api)
        sid = await made(alice, ah)
        begin = "-----BEGIN " + "PRIVATE" + " KEY-----"
        samples = {
            "a long word": b"x" * 3_000_000,
            "dots": b"a." * 1_500_000,
            "key markers": (begin.encode() + b"\n") * 100_000,
            "assignments": b"password=" * 300_000,
            "bearers": b"Bearer " * 400_000,
            "mail-like": b"a@a." * 700_000,
        }
        for name, data in samples.items():
            who, headers = (alice, ah) if len(data) % 2 else (maria, mh)
            started = time.perf_counter()
            r = await put(who, headers, sid, f"{name.replace(' ', '-')}.log", data)
            took = time.perf_counter() - started
            assert r.status_code == 201, (name, r.status_code, r.text[:160])
            assert took < 30, f"{name}: {took:.1f}s for {len(data) // 1024} KB"
        for c in (alice, maria):
            await c.aclose()


# ================================================================================== other people's files
async def test_a_file_cannot_be_reached_through_another_submission_or_another_person(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api, "alice")
        bob, bh = await reporter(api, "bob")
        mine = await made(alice, ah)
        theirs = await made(bob, bh)
        att = (await put(alice, ah, mine, "secret-plan.txt", b"the plan\n")).json()
        # bob uses alice's attachment id under his own submission, and under hers
        for sid in (theirs, mine):
            for tail in ("", "/download", "/preview"):
                r = await bob.get(f"/submissions/{sid}/attachments/{att['id']}{tail}")
                assert r.status_code == 404, (sid == mine, tail, r.status_code)
        assert (await bob.delete(f"/submissions/{theirs}/attachments/{att['id']}", headers=bh)).status_code == 404
        still = await alice.get(f"/submissions/{mine}/attachments/{att['id']}/download")
        assert still.status_code == 200 and still.content == b"the plan\n"
        # nobody can reach the stored bytes by their hash or by any path
        for probe in (att["sha256"], f"sha256-{att['sha256']}", "../artifacts", "restricted"):
            r = await bob.get(f"/submissions/{mine}/attachments/{probe}/download")
            assert r.status_code in (404, 422)
        await alice.aclose()
        await bob.aclose()


async def test_a_session_that_has_ended_or_must_change_its_password_reaches_no_attachment(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        sid = await made(alice, ah)
        att = (await put(alice, ah, sid, "a.txt", b"hello\n")).json()
        out, oh = await reporter(api, "carol")
        await out.post("/auth/logout", headers=oh)
        r = await out.get(f"/submissions/{sid}/attachments/{att['id']}/download")
        assert r.status_code == 401
        accounts(api).create_user("test", "temp-person", role="reporter", password=PASSWORD)
        temp, login = await sign_in(api, "temp-person")
        r = await temp.get(f"/submissions/{sid}/attachments", headers=csrf(login))
        assert r.status_code == 403 and "temporary password" in r.json()["error"]["message"]
        for c in (alice, out, temp):
            await c.aclose()


# ================================================================================================ secrets
async def test_a_secret_in_an_attached_file_is_masked_in_every_copy_that_leaves_the_private_store_and_in_the_log(
    tmp_path: Path,
) -> None:
    with product_logging() as log:
        async with feedback_server(tmp_path) as api:
            alice, ah = await reporter(api)
            maria, _ = await maintainer(api)
            sid = await made(alice, ah)
            data = f"Authorization: Bearer {OPENAI}\nmail jane@example.com\nERROR TimeoutError at step 3\n".encode()
            att = (await put(alice, ah, sid, "run.log", data)).json()
            seen = [
                (await alice.get(f"/submissions/{sid}/attachments")).text,
                (await maria.get(f"/submissions/{sid}/attachments/{att['id']}")).text,
                (await maria.get(f"/submissions/{sid}/attachments/{att['id']}/preview")).text,
                (
                    await maria.get(
                        f"/submissions/{sid}/attachments/{att['id']}/download", params={"version": "sanitized"}
                    )
                ).text,
                (await api.client.get(f"/submissions/{sid}/audit")).text,
            ]
            original = (await alice.get(f"/submissions/{sid}/attachments/{att['id']}/download")).content
            for c in (alice, maria):
                await c.aclose()
    fragment = "Zq81LmN4xW7sR2pK9vB3cD6e"
    for where, blob in (*[(f"response {i}", t) for i, t in enumerate(seen)], ("log", log.getvalue())):
        assert OPENAI not in blob and fragment not in blob and "jane@example.com" not in blob, where
    assert OPENAI.encode() in original, "the original is kept as it was: a file is evidence, and it stays private"
    assert att["secrets"]["status"] == "redacted" and att["secrets"]["external_sharing_blocked"] is False


async def test_a_file_the_second_inspection_doubts_is_marked_for_review_and_not_for_sharing(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        sid = await made(alice, ah)
        opaque = "q9XvK2mLpR7tYw3bNc8Zf5HjD1sGa6Ue"
        att = (
            await put(alice, ah, sid, "trace.log", f"the backend said: session id {opaque} was rejected\n".encode())
        ).json()
        assert att["secrets"]["status"] == "needs_review" and att["secrets"]["external_sharing_blocked"] is True
        preview = (await alice.get(f"/submissions/{sid}/attachments/{att['id']}/preview")).json()
        assert opaque not in preview["text"] and "needs a person's review" in preview["message"]
        await alice.aclose()
