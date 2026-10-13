"""Engineering handoffs over HTTP: who may build and take one, what the answers say, and what the download is.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from agentlab.core.config import HandoffConfig
from tests.api.test_accounts_api import browser
from tests.api.test_feedback_api import ISSUE, feedback_server, maintainer, reporter

OPENAI = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"
LOG = f"2026-10-12 10:00:01 INFO start\nAuthorization: Bearer {OPENAI}\nERROR jane@example.com failed\n".encode()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 120


async def seeded(api, alice, ah):  # type: ignore[no-untyped-def]
    made = await alice.post(
        "/submissions",
        json={**ISSUE, "description": f"My key {OPENAI} is shown", "related_run_id": "run-x1"},
        headers=ah,
    )
    assert made.status_code == 201, made.text
    sid = made.json()["id"]
    up = await alice.post(f"/submissions/{sid}/attachments", files={"file": ("run.log", LOG, "text/plain")}, headers=ah)
    assert up.status_code == 201, up.text
    shot = await alice.post(
        f"/submissions/{sid}/attachments", files={"file": ("screen.png", PNG, "image/png")}, headers=ah
    )
    assert shot.status_code == 201, shot.text
    return sid, up.json()["id"], shot.json()["id"]


async def test_a_maintainer_previews_builds_lists_reads_and_downloads_a_package(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        maria, mh = await maintainer(api)
        sid, _log, shot = await seeded(api, alice, ah)

        preview = await maria.post(f"/submissions/{sid}/handoffs/preview", json={}, headers=mh)
        assert preview.status_code == 200, preview.text
        p = preview.json()
        assert p["status"] == "preview" and p["id"] is None and p["sha256"] is None and p["size"] == 0
        assert p["task"].startswith("# Task for Claude Code: IR-") and p["files"]
        assert any(x["what"] == "attachment screen.png" for x in p["left_out"])
        assert (await maria.get(f"/submissions/{sid}/handoffs")).json() == [], "a preview keeps nothing"

        made = await maria.post(f"/submissions/{sid}/handoffs", json={"include_originals": [shot]}, headers=mh)
        assert made.status_code == 201, made.text
        built = made.json()
        assert built["status"] == "generated" and built["id"] and built["created_by"] == "maria"
        assert built["includes_unsanitized"] is True and built["size"] > 0
        assert any("screen.png" in w for w in built["warnings"])
        assert OPENAI not in made.text and "jane@example.com" not in made.text

        listed = (await maria.get(f"/submissions/{sid}/handoffs")).json()
        assert [h["id"] for h in listed] == [built["id"]]
        read = (await maria.get(f"/handoffs/{built['id']}")).json()
        assert read["sha256"] == built["sha256"] and read["task"] == built["task"]

        zipped = await maria.get(f"/handoffs/{built['id']}/download")
        assert zipped.status_code == 200 and zipped.headers["content-type"] == "application/zip"
        assert zipped.headers["content-disposition"] == f'attachment; filename="agentlab-handoff-{built["number"]}.zip"'
        assert zipped.headers["cache-control"] == "no-store" and zipped.headers["x-content-type-options"] == "nosniff"
        assert zipped.headers["content-security-policy"] == "default-src 'none'; sandbox"
        archive = zipfile.ZipFile(io.BytesIO(zipped.content))
        root = f"agentlab-handoff-{built['number']}/"
        assert all(n.startswith(root) for n in archive.namelist())
        everything = b"".join(archive.read(n) for n in archive.namelist() if not n.endswith(".png"))
        assert OPENAI.encode() not in everything and b"jane@example.com" not in everything
        manifest = json.loads(archive.read(root + "manifest.json"))
        assert manifest["includes_unsanitized_files"] is True and manifest["number"] == built["number"]
        assert zipped.content.startswith(b"PK") and len(zipped.content) == built["size"]

        task = await maria.get(f"/handoffs/{built['id']}/task")
        assert task.status_code == 200 and task.headers["content-type"].startswith("text/markdown")
        assert task.headers["content-disposition"] == 'attachment; filename="CLAUDE_CODE_TASK.md"'
        assert task.text == built["task"] == archive.read(root + "CLAUDE_CODE_TASK.md").decode()

        trail = (await maria.get(f"/submissions/{sid}/audit")).json()
        events = [e["event"] for e in trail]
        assert events.count("handoff_generated") == 1 and events.count("handoff_downloaded") == 2
        assert OPENAI not in json.dumps(trail)
        for client in (alice, maria):
            await client.aclose()


async def test_the_shared_token_may_build_and_a_reporter_may_not_touch_any_of_it(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        sid, _log, _shot = await seeded(api, alice, ah)
        built = await api.client.post(f"/submissions/{sid}/handoffs", json={})
        assert built.status_code == 201, built.text
        hid = built.json()["id"]

        calls = [
            ("POST", f"/submissions/{sid}/handoffs/preview", {}),
            ("POST", f"/submissions/{sid}/handoffs", {}),
            ("GET", f"/submissions/{sid}/handoffs", None),
            ("GET", f"/handoffs/{hid}", None),
            ("GET", f"/handoffs/{hid}/download", None),
            ("GET", f"/handoffs/{hid}/task", None),
        ]
        for method, path, body in calls:
            r = await alice.request(method, path, headers=ah, json=body)
            assert r.status_code == 403, (method, path, r.status_code)
            assert r.json()["error"]["message"] == "only maintainers may do this"
        anonymous = browser(api)
        for method, path, body in calls:
            r = await anonymous.request(method, path, json=body)
            assert r.status_code == 401, (method, path, r.status_code)
        await anonymous.aclose()
        await alice.aclose()


async def test_ids_and_options_are_checked_before_anything_is_built(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        sid, _log, _shot = await seeded(api, alice, ah)
        other = (await alice.post("/submissions", json=ISSUE, headers=ah)).json()["id"]
        theirs = (
            await alice.post(
                f"/submissions/{other}/attachments", files={"file": ("n.txt", b"hello\n", "text/plain")}, headers=ah
            )
        ).json()["id"]
        ghost = "00000000-0000-4000-8000-000000000000"
        assert (await api.client.get(f"/handoffs/{ghost}")).status_code == 404
        assert (await api.client.get(f"/handoffs/{ghost}/download")).status_code == 404
        assert (await api.client.get("/handoffs/x")).status_code == 422, "an id that cannot be one is not looked up"
        assert (await api.client.get("/handoffs/a b c d e f g h")).status_code == 422
        assert (await api.client.post("/submissions/IR-2026-99999/handoffs", json={})).status_code == 404
        assert (await api.client.post(f"/submissions/{sid}/handoffs", json={"unknown": 1})).status_code == 422
        assert (
            await api.client.post(f"/submissions/{sid}/handoffs", json={"include_originals": "x"})
        ).status_code == 422
        assert (
            await api.client.post(f"/submissions/{sid}/handoffs", json={"include_internal_notes": "yes"})
        ).status_code == 422
        named = await api.client.post(f"/submissions/{sid}/handoffs", json={"exclude_attachments": [theirs]})
        assert named.status_code == 422 and "of this submission" in named.text, (
            "another report's file is not named here"
        )
        assert (await api.client.get(f"/submissions/{sid}/handoffs")).json() == []
        await alice.aclose()


async def test_a_server_can_refuse_internal_notes_in_a_package(tmp_path: Path) -> None:
    async with feedback_server(tmp_path, handoff=HandoffConfig(allow_internal_notes=False)) as api:
        alice, ah = await reporter(api)
        sid, _log, _shot = await seeded(api, alice, ah)
        refused = await api.client.post(f"/submissions/{sid}/handoffs", json={"include_internal_notes": True})
        assert refused.status_code == 403 and "allow_internal_notes" in refused.text
        assert (await api.client.post(f"/submissions/{sid}/handoffs", json={})).status_code == 201
        await alice.aclose()


async def test_a_private_security_report_is_not_offered_to_the_shared_token(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        maria, mh = await maintainer(api)
        private = (await alice.post("/submissions", json={**ISSUE, "suspected_security": True}, headers=ah)).json()[
            "id"
        ]
        assert (await api.client.post(f"/submissions/{private}/handoffs", json={})).status_code == 404
        assert (await api.client.post(f"/submissions/{private}/handoffs/preview", json={})).status_code == 404
        made = await maria.post(f"/submissions/{private}/handoffs", json={}, headers=mh)
        assert made.status_code == 201, made.text
        assert (await api.client.get(f"/handoffs/{made.json()['id']}")).status_code == 404
        assert (await api.client.get(f"/handoffs/{made.json()['id']}/download")).status_code == 404
        assert (await maria.get(f"/handoffs/{made.json()['id']}")).status_code == 200
        await alice.aclose()
        await maria.aclose()


async def test_the_api_description_lists_the_handoff_operations_with_their_errors(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        doc = api.app.openapi()
        found = {
            (method.upper(), path): op
            for path, methods in doc["paths"].items()
            if "handoffs" in path
            for method, op in methods.items()
        }
        assert set(found) == {
            ("POST", "/submissions/{submission_id}/handoffs/preview"),
            ("POST", "/submissions/{submission_id}/handoffs"),
            ("GET", "/submissions/{submission_id}/handoffs"),
            ("GET", "/handoffs/{handoff_id}"),
            ("GET", "/handoffs/{handoff_id}/download"),
            ("GET", "/handoffs/{handoff_id}/task"),
        }
        for (method, path), op in found.items():
            assert op["tags"] == ["Feedback & Support"], (method, path)
            assert op["summary"] and "Maintainers only" in op["description"], (method, path)
            assert {"401", "403"} <= set(op["responses"]), (method, path)
        assert "application/zip" in found[("GET", "/handoffs/{handoff_id}/download")]["responses"]["200"]["content"]
        assert "text/markdown" in found[("GET", "/handoffs/{handoff_id}/task")]["responses"]["200"]["content"]
