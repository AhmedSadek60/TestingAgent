"""Showing a report inside the web interface: a short-lived link that opens one file, in a sandbox, without the API token."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx

from agentlab.api.security import FRAMED_CSP, UI_CSP, LinkSigner
from tests.support.api import running_api

TOKEN = "view-" + "token-" + "z" * 16


def swap_last(text: str) -> str:
    return text[:-1] + ("A" if text[-1] != "A" else "B")


# ================================================================================================ the signer
def test_a_link_proves_its_report_its_format_and_its_expiry() -> None:
    signer = LinkSigner(ttl_seconds=300)
    token = signer.sign("report-1", "html", now=1_000.0)
    assert signer.verify(token, now=1_100.0) == ("report-1", "html")
    assert signer.verify(token, now=1_299.0) == ("report-1", "html")
    assert signer.verify(token, now=1_300.0) is None, "it stops working at its expiry"
    assert signer.verify(token, now=9_999.0) is None


def test_a_link_that_was_changed_or_made_elsewhere_is_never_accepted() -> None:
    signer = LinkSigner()
    token = signer.sign("report-1", "html")
    body, mac = token.split(".")
    assert signer.verify(f"{body}.{swap_last(mac)}") is None, "a changed signature"
    forged = base64.urlsafe_b64encode(json.dumps({"r": "report-2", "f": "html", "e": int(time.time()) + 999}).encode())
    assert signer.verify(f"{forged.decode().rstrip('=')}.{mac}") is None, (
        "another report with the first one's signature"
    )
    assert LinkSigner().verify(token) is None, "a link another process (or an earlier run of the server) made"
    for junk in ("", ".", "a.b", "no-dot", "a.b.c", "!!!.???", "x" * 5000, "e30.e30", f"{body}."):
        assert signer.verify(junk) is None, junk


def test_no_other_spelling_of_a_link_is_accepted() -> None:
    """Changing any one character of a link, including the last one of the signature (whose spare bits base64 ignores, so
    the bytes do not change), gives a string that is not the link the signer wrote."""
    signer = LinkSigner()
    token = signer.sign("report-1", "html")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    for position, original in enumerate(token):
        if original == ".":
            continue
        for other in (c for c in alphabet if c != original):
            changed = token[:position] + other + token[position + 1 :]
            assert signer.verify(changed) is None, (position, other)


# ============================================================================================ over the API
async def test_the_html_report_opens_from_a_link_without_a_token_and_only_that_report(tmp_path: Path) -> None:
    async with running_api(tmp_path, token=TOKEN) as api:
        run = await api.run()
        report_id = (await api.client.get(f"/test-runs/{run['id']}/reports")).json()[0]["id"]

        no_html = await api.client.post(f"/reports/{report_id}/view-link")
        assert no_html.status_code == 404 and "export" in no_html.json()["error"]["message"]

        exported = (await api.client.post(f"/reports/{report_id}/export", json={"format": "html"})).json()
        with_html = exported["report"]["id"]
        link = await api.client.post(f"/reports/{with_html}/view-link")
        assert link.status_code == 200
        body = link.json()
        assert body["expires_in"] == 300 and body["url"].startswith("/view/")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
        ) as anonymous:
            assert (await anonymous.post(f"/reports/{with_html}/view-link")).status_code == 401, (
                "minting needs the token"
            )
            assert (await anonymous.get(f"/reports/{with_html}/files/html")).status_code == 401
            opened = await anonymous.get(body["url"])
            assert opened.status_code == 200 and opened.headers["content-type"].startswith("text/html")
            assert "AgentLab" in opened.text
            csp = opened.headers["content-security-policy"]
            assert csp == FRAMED_CSP and "sandbox allow-scripts" in csp and "default-src 'none'" in csp
            assert "frame-ancestors 'self'" in csp
            assert opened.headers["x-frame-options"] == "SAMEORIGIN"
            assert opened.headers["cache-control"] == "no-store" and opened.headers["referrer-policy"] == "no-referrer"
            assert opened.headers["x-content-type-options"] == "nosniff"

            token = body["url"].removeprefix("/view/")
            payload, mac = token.split(".")
            for bad in (f"{payload}.{swap_last(mac)}", "garbage", f"{swap_last(payload)}.{mac}"):
                refused = await anonymous.get(f"/view/{bad}")
                assert refused.status_code == 404, bad
                assert refused.json()["error"]["kind"] == "not_found"
                assert "<html" not in refused.text.lower()

            expired = api.state.links.sign(with_html, "html", now=time.time() - 400)
            assert (await anonymous.get(f"/view/{expired}")).status_code == 404
            other_format = api.state.links.sign(with_html, "pdf")
            assert (await anonymous.get(f"/view/{other_format}")).status_code == 404, (
                "a format the report does not have"
            )
            unknown = api.state.links.sign("no-such-report", "html")
            assert (await anonymous.get(f"/view/{unknown}")).status_code == 404


async def test_the_interface_may_frame_only_this_server_and_the_view_is_not_in_the_reference(tmp_path: Path) -> None:
    assert "frame-src 'self'" in UI_CSP and "blob:" not in UI_CSP.split("frame-src")[1].split(";")[0]
    async with running_api(tmp_path) as api:
        schema = api.app.openapi()
        assert "/reports/{report_id}/view-link" in schema["paths"]
        assert not [p for p in schema["paths"] if p.startswith("/view")], "a capability link is not an API operation"
        assert (await api.client.get("/view/anything")).status_code == 404
