"""Publishing a submission over HTTP: who may, what is previewed, and what is answered when GitHub says no.

GitHub is a fake that keeps its own issues; nothing here leaves the machine.
Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentlab.core.config import IntegrationsConfig
from agentlab.feedback.integrations.github import GitHubTracker
from tests.api.test_accounts_api import PASSWORD, _own_password, accounts, browser, csrf, sign_in
from tests.api.test_feedback_api import ISSUE, feedback_server, reporter
from tests.unit.test_feedback_publication import REPO, TOKEN, FakeGitHub, github_config


def wire(api: Any, fake: FakeGitHub) -> None:
    """Point the server's tracker at the fake: nothing in these tests may reach the real GitHub."""
    st = api.app.state.agentlab
    cfg = st.services.config.feedback.integrations.github
    st.publications.trackers = {
        "github": GitHubTracker(cfg, lambda: TOKEN, transport=fake.transport(), clock=lambda: 1_000.0)
    }


async def administrator(api: Any, name: str = "root"):  # type: ignore[no-untyped-def]
    svc = accounts(api)
    user, _ = svc.create_user("test", name, role="admin", password=PASSWORD)
    _own_password(svc, user.id)
    client, login = await sign_in(api, name)
    return client, csrf(login)


def enabled(**over: Any) -> dict[str, Any]:
    return {"integrations": IntegrationsConfig(github=github_config(**over))}


async def test_an_administrator_previews_publishes_and_sees_the_issue_and_the_reporter_sees_none_of_it(
    tmp_path: Path,
) -> None:
    fake = FakeGitHub()
    async with feedback_server(tmp_path, **enabled(publish_roles=["admin"])) as api:
        wire(api, fake)
        alice, ah = await reporter(api)
        root, rh = await administrator(api)
        made = (await alice.post("/submissions", json=ISSUE, headers=ah)).json()
        sid = made["id"]

        listed = await root.get("/integrations")
        assert listed.status_code == 200
        assert listed.json() == [
            {
                "provider": "github",
                "enabled": True,
                "ready": True,
                "reason": None,
                "repository": REPO,
                "may_publish": True,
                "status_sync": True,
            }
        ]
        preview = await root.post(f"/submissions/{sid}/publications/preview", json={"provider": "github"}, headers=rh)
        assert preview.status_code == 200, preview.text
        p = preview.json()
        assert (
            p["repository"] == REPO and p["title"].startswith(f"[{made['number']}] ") and len(p["content_hash"]) == 64
        )
        assert "attachments and screenshots" in p["left_out"] and p["already"] is None
        assert fake.requests == [], "a preview calls nobody"

        done = await root.post(
            f"/submissions/{sid}/publications",
            json={"provider": "github", "content_hash": p["content_hash"]},
            headers=rh,
        )
        assert done.status_code == 201, done.text
        link = done.json()
        assert (
            link["state"] == "published"
            and link["url"] == f"https://github.com/{REPO}/issues/1"
            and link["number"] == 1
        )
        assert TOKEN not in done.text
        assert (await root.get(f"/submissions/{sid}/publications")).json()[0]["id"] == link["id"]
        trail = (await root.get(f"/submissions/{sid}/audit")).json()
        assert [e["event"] for e in trail].count("publication_published") == 1

        # the reporter is told nothing of it, anywhere
        seen = await alice.get(f"/submissions/{sid}", headers=ah)
        assert "github" not in seen.text.lower() and "publication" not in seen.text.lower()
        for method, path, body in (
            ("GET", "/integrations", None),
            ("POST", f"/submissions/{sid}/publications/preview", {"provider": "github"}),
            ("POST", f"/submissions/{sid}/publications", {"provider": "github", "content_hash": "0" * 64}),
            ("GET", f"/submissions/{sid}/publications", None),
            ("POST", f"/submissions/{sid}/publications/github/sync", {}),
        ):
            r = await alice.request(method, path, headers=ah, json=body)
            assert r.status_code == 403, (method, path, r.status_code)
        anonymous = browser(api)
        assert (await anonymous.get("/integrations")).status_code == 401
        for client in (alice, root, anonymous):
            await client.aclose()


async def test_nothing_is_offered_and_nothing_is_sent_while_the_integration_is_off(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        made = (await alice.post("/submissions", json=ISSUE, headers=ah)).json()
        listed = (await api.client.get("/integrations")).json()
        assert listed[0]["enabled"] is False and listed[0]["ready"] is False and "switched off" in listed[0]["reason"]
        r = await api.client.post(f"/submissions/{made['id']}/publications/preview", json={"provider": "github"})
        assert r.status_code == 422 and "not switched on" in r.text
        assert api.app.state.agentlab.publications.trackers == {}
        await alice.aclose()


async def test_a_private_report_a_stale_preview_and_the_rate_limit_are_each_answered_in_words(tmp_path: Path) -> None:
    fake = FakeGitHub()
    async with feedback_server(tmp_path, **enabled(publish_roles=["maintainer", "admin"])) as api:
        wire(api, fake)
        alice, ah = await reporter(api)
        public = (await alice.post("/submissions", json=ISSUE, headers=ah)).json()
        private = (await alice.post("/submissions", json={**ISSUE, "suspected_security": True}, headers=ah)).json()
        api_client = api.client  # the shared token acts as a maintainer, and may see public submissions
        refused = await api_client.post(
            f"/submissions/{private['id']}/publications/preview", json={"provider": "github"}
        )
        assert refused.status_code == 404, "the shared token does not even see a private report"
        root, rh = await administrator(api)
        refused = await root.post(
            f"/submissions/{private['id']}/publications/preview", json={"provider": "github"}, headers=rh
        )
        assert refused.status_code == 403 and "never published" in refused.text

        stale = await api_client.post(
            f"/submissions/{public['id']}/publications", json={"provider": "github", "content_hash": "a" * 64}
        )
        assert stale.status_code == 409 and "not what you previewed" in stale.text
        h = (
            await api_client.post(f"/submissions/{public['id']}/publications/preview", json={"provider": "github"})
        ).json()["content_hash"]
        fake.fail.append((f"POST /repos/{REPO}/issues", 429, {"retry-after": "120"}, {"message": "Too Many Requests"}))
        limited = await api_client.post(
            f"/submissions/{public['id']}/publications", json={"provider": "github", "content_hash": h}
        )
        assert limited.status_code == 429 and limited.headers["retry-after"] == "120"
        assert "about 2 minute" in limited.text and fake.issues == []
        ok = await api_client.post(
            f"/submissions/{public['id']}/publications", json={"provider": "github", "content_hash": h}
        )
        assert ok.status_code == 201 and len(fake.issues) == 1
        bad = await api_client.post(
            f"/submissions/{public['id']}/publications",
            json={"provider": "github", "content_hash": h, "acknowledge_unreviewed": "yes"},
        )
        assert bad.status_code == 422, "agreement is the JSON value true, not a word"
        await alice.aclose()
        await root.aclose()


async def test_a_status_is_pushed_to_the_issue_on_request(tmp_path: Path) -> None:
    fake = FakeGitHub()
    async with feedback_server(tmp_path, **enabled(publish_roles=["maintainer"])) as api:
        wire(api, fake)
        alice, ah = await reporter(api)
        made = (await alice.post("/submissions", json=ISSUE, headers=ah)).json()
        sid = made["id"]
        h = (await api.client.post(f"/submissions/{sid}/publications/preview", json={"provider": "github"})).json()[
            "content_hash"
        ]
        await api.client.post(f"/submissions/{sid}/publications", json={"provider": "github", "content_hash": h})
        await api.client.patch(f"/submissions/{sid}", json={"version": made["version"], "status": "Closed"})
        synced = await api.client.post(f"/submissions/{sid}/publications/github/sync")
        assert synced.status_code == 200, synced.text
        assert synced.json()["synced_status"] == "Closed" and synced.json()["external_state"] == "closed"
        assert fake.issues[0]["state"] == "closed"
        await alice.aclose()


async def test_the_api_description_lists_the_publication_operations_with_their_errors(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        doc = api.app.openapi()
        found = {
            (method.upper(), path): op
            for path, methods in doc["paths"].items()
            if "publication" in path or path == "/integrations"
            for method, op in methods.items()
        }
        assert set(found) == {
            ("GET", "/integrations"),
            ("POST", "/submissions/{submission_id}/publications/preview"),
            ("POST", "/submissions/{submission_id}/publications"),
            ("GET", "/submissions/{submission_id}/publications"),
            ("POST", "/submissions/{submission_id}/publications/{provider}/sync"),
        }
        for key, op in found.items():
            assert op["tags"] == ["Feedback & Support"] and op["summary"] and len(op["description"]) > 40, key
            assert {"401", "403"} <= set(op["responses"]), key
        assert {"409", "429"} <= set(found[("POST", "/submissions/{submission_id}/publications")]["responses"])
