"""Diagnostics over HTTP: what can be collected, reviewing it, consenting, attaching, and downloading.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import Any

import httpx

from agentlab.core.config import DiagnosticsConfig
from agentlab.feedback.logcapture import LogCapture
from tests.api.test_feedback_api import ISSUE, feedback_server, maintainer, reporter

OPENAI = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"


def flush_logs() -> None:
    """The capture writes in the background; a test that needs the lines now writes them."""
    for handler in logging.getLogger("agentlab").handlers:
        if isinstance(handler, LogCapture):
            handler.write_batch()


async def collect(client: httpx.AsyncClient, headers: dict[str, str], body: dict[str, Any]) -> httpx.Response:
    flush_logs()
    return await client.post("/diagnostics/collect", json=body, headers=headers)


# ====================================================================================== the whole path
async def test_a_reporter_sees_what_can_be_collected_reviews_it_consents_attaches_and_downloads(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        menu = (await alice.get("/diagnostics/available")).json()
        by_id = {s["id"]: s for s in menu["sources"]}
        assert by_id["environment"]["available"] and by_id["run_metadata"]["available"] is False
        assert "maintainers only" in by_id["trace"]["reason"] and by_id["screenshot"]["manual"] is True
        assert menu["windows"] == ["last_5_minutes", "last_15_minutes", "last_30_minutes", "custom"]
        assert menu["consent_text"].startswith("The diagnostic information you attach")

        failed = await alice.get("/test-runs/some-run")  # a reporter is refused: a line the server logs
        assert failed.status_code == 403
        request_id = failed.headers["x-request-id"]
        assert len(request_id) == 16

        body = {
            "categories": ["environment", "application_logs", "backend_logs", "browser", "screenshot"],
            "browser": {
                "console": [{"level": "error", "message": f"TypeError at fetch(https://app.test/x?token={OPENAI})"}],
                "network": [
                    {
                        "method": "GET",
                        "url": "https://app.test/test-runs/some-run",
                        "status": 403,
                        "request_id": request_id,
                    }
                ],
            },
        }
        flush_logs()
        preview = await alice.post("/diagnostics/preview", json=body, headers=ah)
        assert preview.status_code == 200 and preview.json()["id"] is None and preview.json()["status"] == "preview"
        collected = await alice.post("/diagnostics/collect", json=body, headers=ah)
        assert collected.status_code == 201, collected.text
        draft = collected.json()
        assert draft["status"] == "draft" and draft["expires_at"] and OPENAI not in collected.text
        cats = {(i["category"], i["outcome"]) for i in draft["items"]}
        assert {("environment", "collected"), ("application_logs", "collected"), ("browser", "collected")} <= cats
        assert ("screenshot", "unavailable") in cats and ("backend_logs", "unavailable") in cats
        logs = next(i for i in draft["items"] if i["category"] == "application_logs")
        assert f"[req {request_id}]" in logs["preview"] and "answered 403" in logs["preview"]
        assert any("could not be included" in w for w in draft["warnings"])

        made = (await alice.post("/submissions", json=ISSUE, headers=ah)).json()
        sid = made["id"]
        refused = await alice.post(
            f"/submissions/{sid}/diagnostics", json={"collection_id": draft["id"], "consent": False}, headers=ah
        )
        assert refused.status_code == 422 and "explicit consent" in refused.json()["error"]["message"]
        for not_true in ("yes", "true", 1):
            odd = await alice.post(
                f"/submissions/{sid}/diagnostics", json={"collection_id": draft["id"], "consent": not_true}, headers=ah
            )
            assert odd.status_code == 422, not_true
        done = await alice.post(
            f"/submissions/{sid}/diagnostics", json={"collection_id": draft["id"], "consent": True}, headers=ah
        )
        assert done.status_code == 201 and done.json()["status"] == "attached" and done.json()["consent_at"]

        listed = (await alice.get(f"/submissions/{sid}/diagnostics")).json()
        assert [c["id"] for c in listed] == [draft["id"]]
        archive = await alice.get(f"/diagnostics/{draft['id']}/download")
        assert archive.status_code == 200 and archive.headers["content-type"] == "application/zip"
        assert "attachment" in archive.headers["content-disposition"] and "no-store" in archive.headers["cache-control"]
        zf = zipfile.ZipFile(io.BytesIO(archive.content))
        assert "diagnostics/manifest.json" in zf.namelist() and "diagnostics/environment.json" in zf.namelist()
        assert OPENAI not in b"".join(zf.read(n) for n in zf.namelist()).decode()

        maria, _ = await maintainer(api)
        seen = await maria.get(f"/submissions/{sid}/diagnostics")
        assert seen.status_code == 200 and seen.json()[0]["id"] == draft["id"]
        trail = (await maria.get(f"/submissions/{sid}/audit")).json()
        assert {"diagnostics_attached", "diagnostics_downloaded"} <= {e["event"] for e in trail}
        await alice.aclose()
        await maria.aclose()


async def test_a_maintainer_collects_the_evidence_of_a_run(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        run = await api.run()
        run_id = run["id"]
        store = api.services.store
        traced = next(t for t in store.list_traces(run_id) if t.get("artifact_id"))
        test_id = traced["test_key"]
        menu = (await api.client.get("/diagnostics/available", params={"run_id": run_id, "test_id": test_id})).json()
        assert {s["id"]: s["available"] for s in menu["sources"]}["trace"] is True
        r = await api.client.post(
            "/diagnostics/collect",
            json={
                "categories": ["run_metadata", "test_summary", "trace", "environment"],
                "run_id": run_id,
                "test_id": test_id,
            },
        )
        assert r.status_code == 201, r.text
        outcomes = {i["category"]: i["outcome"] for i in r.json()["items"]}
        assert outcomes == {
            "run_metadata": "collected",
            "test_summary": "collected",
            "trace": "collected",
            "environment": "collected",
        }
        trace = next(i for i in r.json()["items"] if i["category"] == "trace")
        assert (
            trace["size"] > 0
            and '"events"' in trace["preview"]
            and trace["redaction"]["status"] in {"clean", "redacted"}
        )


# ================================================================================================ requests
async def test_a_bad_request_is_refused_and_says_which_field(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        for body, message in (
            ({"categories": []}, "categories"),
            ({"categories": ["nonsense"]}, "categories"),
            ({"categories": ["environment"], "window": "last_week"}, "window"),
            ({"categories": ["environment"], "window": "custom"}, "window_from"),
            (
                {
                    "categories": ["environment"],
                    "window": "custom",
                    "window_from": "2026-10-12T10:00:00Z",
                    "window_to": "2026-10-12T09:00:00Z",
                },
                "window_to",
            ),
            ({"categories": ["environment"], "run_id": "../../etc/passwd"}, "run_id"),
            ({"categories": ["environment"], "extra": 1}, "extra"),
            ({"categories": ["browser"], "browser": {"console": [{"message": "x" * 3000}]}}, "message"),
        ):
            r = await alice.post("/diagnostics/preview", json=body, headers=ah)
            assert r.status_code == 422, (body, r.status_code)
            assert message in r.json()["error"]["message"], (body, r.json())
        too_long = await alice.post(
            "/diagnostics/preview",
            json={
                "categories": ["application_logs"],
                "window": "custom",
                "window_from": "2026-10-12T00:00:00Z",
                "window_to": "2026-10-12T20:00:00Z",
            },
            headers=ah,
        )
        assert too_long.status_code == 422 and "at most 360 minutes" in too_long.json()["error"]["message"]
        await alice.aclose()


async def test_a_change_from_a_browser_session_needs_the_csrf_token(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        body = {"categories": ["environment"]}
        for path in ("/diagnostics/preview", "/diagnostics/collect", "/diagnostics/redact-preview"):
            payload = {"text": "hello"} if path.endswith("redact-preview") else body
            assert (await alice.post(path, json=payload)).status_code == 403, path
        assert (await alice.post("/diagnostics/collect", json=body, headers=ah)).status_code == 201
        await alice.aclose()


async def test_text_can_be_seen_masked_before_it_is_pasted(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        r = await alice.post(
            "/diagnostics/redact-preview",
            json={
                "text": f"curl -H 'Authorization: Bearer {OPENAI}' https://x.test\nmail jane@example.com\nplain line\n"
            },
            headers=ah,
        )
        assert r.status_code == 200
        body = r.json()
        assert OPENAI not in r.text and "jane@example.com" not in r.text and "plain line" in body["text"]
        assert body["redaction"]["status"] == "redacted" and body["redaction"]["counts"]
        assert any("line" in loc for loc in body["redaction"]["locations"])
        assert "does not guarantee" in body["note"] and "patterns" in body["mechanisms"]
        await alice.aclose()


async def test_the_server_gives_every_response_an_id_and_ignores_one_a_client_sends(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        first = await api.client.get("/health")
        second = await api.client.get("/health", headers={"X-Request-Id": "abcdef0123456789"})
        assert first.headers["x-request-id"] != second.headers["x-request-id"]
        assert second.headers["x-request-id"] != "abcdef0123456789", (
            "an id is made by the server, never taken from a client"
        )
        refused = await api.client.post("/projects", content=b"{", headers={"Content-Type": "application/json"})
        assert refused.status_code == 422 and len(refused.headers["x-request-id"]) == 16


async def test_the_api_description_lists_the_diagnostic_operations(tmp_path: Path) -> None:
    async with feedback_server(tmp_path, diagnostics=DiagnosticsConfig()) as api:
        doc = api.app.openapi()
        found = {
            (m.upper(), p)
            for p, methods in doc["paths"].items()
            if p.startswith("/diagnostics") or p.endswith("/diagnostics")
            for m in methods
        }
        assert found == {
            ("GET", "/diagnostics/available"),
            ("POST", "/diagnostics/preview"),
            ("POST", "/diagnostics/collect"),
            ("POST", "/diagnostics/redact-preview"),
            ("GET", "/diagnostics/{collection_id}"),
            ("GET", "/diagnostics/{collection_id}/download"),
            ("POST", "/submissions/{submission_id}/diagnostics"),
            ("GET", "/submissions/{submission_id}/diagnostics"),
        }
