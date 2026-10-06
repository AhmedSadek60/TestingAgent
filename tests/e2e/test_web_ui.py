"""The web interface in a real browser against a real ``agentlab serve``.

Everything here is what a person does: open the page the server serves, fill in the wizard, watch a run, read the results,
review a finding, open a report, compare two runs, store a credential, stop a run, sign in with a token. The server is the
product's own process (not a test double); the agents under test are the deterministic in-process mock agents.

The interface must be built first (``cd web && npm ci && npm run build``, which writes ``src/agentlab/api/static``);
without that, or without Playwright and a Chromium build, these tests are skipped and say why.

Whatever the page does wrong on its own (a script error, a console warning, a request that fails) fails the test that
caused it. Secrets are made up from pieces so that no scanner takes them for real ones.
"""

from __future__ import annotations

import re
import signal
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentlab.api.app import default_ui_dir
from agentlab.browser.environment import find_chromium
from agentlab.core.config import AgentLabConfig, ServerConfig
from tests.support.api import SMALL_RUN, TERMINAL, api_config, mock_target
from tests.support.browser import browser_ok, needs_browser
from tests.support.process import Proc, free_port, poll, start_agentlab, wait_until_up, write_config

if browser_ok():  # the import itself is what a machine without Playwright cannot do
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright, expect, sync_playwright
else:  # pragma: no cover - the tests are skipped then
    Browser = BrowserContext = Page = Playwright = Any  # type: ignore[misc,assignment]

    def expect(*_: Any, **__: Any) -> Any:  # type: ignore[misc]
        raise RuntimeError("Playwright is not available")

    def sync_playwright() -> Any:  # type: ignore[misc]
        raise RuntimeError("Playwright is not available")


pytestmark = [
    pytest.mark.skipif(
        default_ui_dir() is None or not (default_ui_dir() or Path()).joinpath("index.html").exists(),
        reason="the web interface is not built (cd web && npm ci && npm run build)",
    ),
]

TOKEN = "e2e-" + "token-" + "z" * 20
CLEAN_AGENT = "Clean HR assistant"
FLAWED_AGENT = "Flawed HR assistant"
IGNORED_FAILURES = ("net::ERR_ABORTED",)  # a page that goes away cancels what it was loading: not a fault


# ======================================================================================================== the servers
@dataclass
class Lab:
    base: str
    proc: Proc
    root: Path
    runs: dict[str, str] = field(default_factory=dict)

    def client(self, token: str | None = None) -> httpx.Client:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx.Client(base_url=self.base, timeout=60, headers=headers)

    def start_run(self, target: dict[str, Any], token: str | None = None, **extra: Any) -> str:
        with self.client(token) as c:
            r = c.post("/test-runs", json={"target": target, "options": SMALL_RUN, **extra})
            assert r.status_code == 202, r.text
            return str(r.json()["run_id"])

    def wait(self, run_id: str, token: str | None = None, timeout: float = 120) -> dict[str, Any]:
        with self.client(token) as c:
            return poll(
                lambda: (r := c.get(f"/test-runs/{run_id}").json())["status"] in TERMINAL and r,
                timeout=timeout,
                every=0.2,
                what=f"run {run_id} ending",
            )


def serve(root: Path, *, env: dict[str, str] | None = None, server: ServerConfig | None = None) -> Lab:
    config = api_config(root, formats=["json", "md", "html"], **({"server": server} if server else {}))
    write_config(root, config)
    port = free_port()
    proc = start_agentlab(
        ["--config", str(root / "agentlab.yaml"), "serve", "--port", str(port)], cwd=root, env=env, name="serve"
    )
    base = f"http://127.0.0.1:{port}"
    wait_until_up(f"{base}/health", proc)
    return Lab(base=base, proc=proc, root=root)


@pytest.fixture(scope="module")
def lab(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Lab]:
    """One server for the module, with a clean run and a flawed run already in it."""
    root = tmp_path_factory.mktemp("web-ui")
    lab = serve(root)
    try:
        clean = lab.start_run(mock_target(CLEAN_AGENT, "success"))
        flawed = lab.start_run(
            mock_target(FLAWED_AGENT, "unsafe_behavior", "hallucination", "wrong_argument", "prompt_injection")
        )
        lab.runs = {"clean": clean, "flawed": flawed}
        assert lab.wait(clean)["status"] == "completed"
        assert lab.wait(flawed)["status"] == "completed"
        yield lab
    finally:
        code = lab.proc.stop(signal.SIGTERM)
        assert code == 0, lab.proc.output
        assert "Traceback" not in lab.proc.output, lab.proc.output


@pytest.fixture(scope="module")
def protected(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Lab]:
    """A server that asks for a token, as one reachable by other people would."""
    root = tmp_path_factory.mktemp("web-ui-protected")
    lab = serve(
        root,
        env={"AGENTLAB_API_TOKEN": TOKEN},
        server=ServerConfig(token_ref="env:AGENTLAB_API_TOKEN"),
    )
    try:
        yield lab
    finally:
        assert lab.proc.stop(signal.SIGTERM) == 0, lab.proc.output
        assert TOKEN not in lab.proc.output, "the token is never printed or logged"


# ======================================================================================================== the browser
@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(
            executable_path=find_chromium(AgentLabConfig()), headless=True, args=["--no-sandbox"]
        )
        try:
            yield chromium
        finally:
            chromium.close()


class Watched:
    """A page, and everything it did that it should not have."""

    def __init__(self, context: BrowserContext, allow_status: tuple[int, ...] = ()) -> None:
        self.allow_status = allow_status
        self.context = context
        self.page: Page = context.new_page()
        self.problems: list[str] = []
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.page.on("console", self._console)
        self.page.on("pageerror", lambda e: self.problems.append(f"page error: {e}"))
        self.page.on("response", lambda r: self._response(r, allow_status))
        self.page.on("requestfailed", self._failed)
        self.page.on("request", lambda r: self.requests.append((r.url, dict(r.headers))))

    def _console(self, message: Any) -> None:
        if message.type not in ("error", "warning"):
            return
        # the browser itself reports every refused request; one the test expects (a wrong token) is not a fault
        refused = re.search(r"status of (\d{3})", message.text)
        if (
            message.text.startswith("Failed to load resource")
            and refused
            and int(refused.group(1)) in self.allow_status
        ):
            return
        self.problems.append(f"console {message.type}: {message.text}")

    def _response(self, response: Any, allow: tuple[int, ...]) -> None:
        if response.status >= 400 and response.status not in allow:
            self.problems.append(f"HTTP {response.status} {response.request.method} {response.url}")

    def _failed(self, request: Any) -> None:
        failure = request.failure or ""
        if not any(ignored in failure for ignored in IGNORED_FAILURES):
            self.problems.append(f"request failed: {request.url} {failure}")


@pytest.fixture
def ui(browser: Browser) -> Iterator[Watched]:
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    watched = Watched(context)
    try:
        yield watched
    finally:
        context.close()
    assert not watched.problems, "the page misbehaved:\n" + "\n".join(watched.problems)


def tabs(page: Page, name: str) -> None:
    page.get_by_role("navigation", name="Run", exact=True).get_by_role("link", name=name, exact=True).click()


def stored(page: Page) -> dict[str, dict[str, str]]:
    """What the page keeps in this browser, by kind of storage."""
    result: dict[str, dict[str, str]] = page.evaluate(
        "() => ({local: Object.fromEntries(Object.entries(localStorage)), session: Object.fromEntries(Object.entries(sessionStorage))})"
    )
    return result


# ====================================================================================================== the first visit
@needs_browser
def test_the_server_serves_the_interface_with_a_strict_policy_and_the_page_opens_cleanly(lab: Lab, ui: Watched) -> None:
    index = httpx.get(lab.base + "/")
    assert index.status_code == 200 and "text/html" in index.headers["content-type"]
    policy = index.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "connect-src 'self'" in policy and "object-src 'none'" in policy
    assert "'unsafe-eval'" not in policy and "script-src 'unsafe-inline'" not in policy
    assert index.headers["x-content-type-options"] == "nosniff"
    assert index.headers["x-frame-options"] == "DENY"

    ui.page.goto(lab.base + "/#/")
    expect(ui.page.get_by_role("heading", name="Dashboard", level=1)).to_be_visible()
    expect(ui.page.get_by_text(FLAWED_AGENT).first).to_be_visible()
    assert "agentlab.token" not in stored(ui.page)["session"], "a server that asks for no token leaves none behind"


# ====================================================================================================== the wizard
@needs_browser
def test_the_wizard_designs_a_plan_starts_a_run_and_follows_it_to_the_end(lab: Lab, ui: Watched) -> None:
    page = ui.page
    page.goto(lab.base + "/#/new")
    page.get_by_role("button", name=re.compile("Demonstration agent")).click()
    page.get_by_role("button", name="Next").click()
    page.get_by_label("Name", exact=True).fill("Wizard demo assistant")
    page.get_by_label(re.compile("Plant defects")).check()
    page.get_by_role("button", name="Next").click()  # credentials: none
    page.get_by_role("button", name="Next").click()
    page.get_by_label(re.compile("What do you want to learn")).fill(
        "Can this assistant be trusted with leave requests?"
    )
    page.get_by_role("button", name="Next").click()
    page.get_by_role("button", name=re.compile("^Quick")).click()
    page.get_by_role("button", name="Next").click()
    page.get_by_label("At most this many tests").fill("6")
    page.get_by_label(re.compile("Add follow-up tests")).uncheck()
    page.get_by_role("button", name="Next").click()

    # the plan is explained before anything runs
    expect(page.get_by_text("Tests selected")).to_be_visible(timeout=60_000)
    with httpx.Client(base_url=lab.base) as c:
        assert not [
            r for r in c.get("/test-runs").json() if r["target"] == "Wizard demo assistant" and r["kind"] == "run"
        ]
    page.get_by_role("button", name="Next: execute").click()
    page.get_by_role("button", name=re.compile("Start the run")).click()
    page.wait_for_url(re.compile("#/runs/"), timeout=15_000)

    # the live view follows the run to its end without a reload
    expect(page.get_by_text("The run is completed")).to_be_visible(timeout=120_000)
    for tab in ["Results", "Findings", "Scorecard", "Traces", "Reports", "Plan"]:
        tabs(page, tab)
        expect(
            page.get_by_role("navigation", name="Run", exact=True).get_by_role("link", name=tab, exact=True)
        ).to_have_attribute("aria-current", "page")
        page.wait_for_load_state("networkidle")


# ====================================================================================================== reading a run
@needs_browser
def test_results_can_be_filtered_searched_and_opened_and_the_agents_words_stay_text(lab: Lab, ui: Watched) -> None:
    page = ui.page
    page.goto(f"{lab.base}/#/runs/{lab.runs['flawed']}/results")
    expect(page.get_by_role("button", name=re.compile(r"^Failed \("))).to_be_visible()
    page.get_by_role("button", name=re.compile(r"^Failed \(")).click()
    rows = page.get_by_role("row")
    assert rows.count() > 1
    rows.nth(1).click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text(re.compile("untrusted, shown as text")).first).to_be_visible()
    assert (
        page.evaluate("() => document.querySelectorAll('.untrusted img, .untrusted script, .untrusted iframe').length")
        == 0
    )
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    page.get_by_label("Search results").fill("EXFIL")
    page.wait_for_timeout(300)
    assert page.get_by_role("row").count() >= 1


@needs_browser
def test_a_reviewer_can_confirm_and_reject_findings_and_the_original_evaluation_stays_on_record(
    lab: Lab, ui: Watched
) -> None:
    page = ui.page
    run_id = lab.runs["flawed"]
    with httpx.Client(base_url=lab.base) as c:
        findings_before = c.get(f"/test-runs/{run_id}/findings").json()
        results_before = c.get(f"/test-runs/{run_id}/results").json()
    assert len(findings_before) >= 3, "the flawed agent has findings"
    page.goto(f"{lab.base}/#/runs/{run_id}/findings")
    expect(page.get_by_text("Severity distribution")).to_be_visible()
    listed = page.get_by_role("list", name="Findings").get_by_role("listitem")
    expect(listed).to_have_count(len(findings_before))

    # confirm one: the review is shown with the finding, under the reviewer's name
    listed.nth(2).click()
    expect(listed.nth(2)).to_have_attribute("aria-current", "true")  # the form below is now the one for this finding
    page.get_by_label("Your name").fill("Reviewer One")
    page.get_by_label(re.compile("^Reason")).fill("Seen in the trace")
    page.get_by_role("button", name="Save review").click()
    expect(page.get_by_text("Review saved. The original evaluation is kept.").first).to_be_visible()
    expect(page.get_by_text("Reviewer One").first).to_be_visible()

    # reject another: it leaves the list until the person asks to see rejected findings, and is not deleted
    listed.first.click()
    expect(listed.first).to_have_attribute("aria-current", "true")
    page.get_by_label("Decision").select_option(label="False positive")
    page.get_by_label("Your name").fill("Reviewer Two")
    page.get_by_label(re.compile("^Reason")).fill("The agent only quoted the policy")
    page.get_by_role("button", name="Save review").click()
    expect(listed).to_have_count(len(findings_before) - 1)
    page.get_by_label("Show findings a reviewer rejected").click()
    expect(listed).to_have_count(len(findings_before))

    with httpx.Client(base_url=lab.base) as c:
        reviews = c.get(f"/test-runs/{run_id}/reviews").json()
        assert {(r["reviewer"], r["decision"]) for r in reviews} == {
            ("Reviewer One", "approve"),
            ("Reviewer Two", "false_positive"),
        }
        assert all(r["original"] for r in reviews), "what the evaluation said before the review is kept beside it"
        assert len(c.get(f"/test-runs/{run_id}/findings").json()) == len(findings_before), "nothing is deleted"
        results_after = c.get(f"/test-runs/{run_id}/results").json()
    assert [(r["test_id"], r["status"], r["score"]) for r in results_after] == [
        (r["test_id"], r["status"], r["score"]) for r in results_before
    ], "the evaluation itself is not rewritten by a review"
    assert stored(page)["local"].get("agentlab.reviewer") == "Reviewer Two"


@needs_browser
def test_the_scorecard_and_the_traces_open(lab: Lab, ui: Watched) -> None:
    page = ui.page
    page.goto(f"{lab.base}/#/runs/{lab.runs['flawed']}/scorecard")
    expect(page.get_by_role("heading", name="Categories")).to_be_visible()
    expect(page.get_by_role("img", name=re.compile(r"^Score .* of 100")).first).to_be_visible()
    page.goto(f"{lab.base}/#/runs/{lab.runs['flawed']}/traces")
    expect(page.get_by_text(re.compile(r"Traces \("))).to_be_visible()
    page.get_by_role("list", name="Traces").get_by_role("listitem").first.click()
    expect(page.get_by_role("heading", level=2).first).to_be_visible()


# ============================================================================================ hostile agent text
EVIL = '<img src=x onerror="window.__pwned = 1"><script>window.__pwned = 1</script><a href="javascript:window.__pwned = 1">x</a>'


def nothing_ran(page: Page) -> bool:
    """No script that came out of the agent's words ran in the page or in any frame inside it."""
    return all(frame.evaluate("window.__pwned === undefined") for frame in page.frames)


def only_text(page: Page) -> None:
    assert page.locator("img[src='x']").count() == 0, "markup from the agent became an element"
    assert page.locator("a[href^='javascript:']").count() == 0
    assert nothing_ran(page), "a script from the agent ran"


@needs_browser
def test_what_the_agent_calls_itself_is_shown_as_text_on_every_screen_and_never_runs(lab: Lab, ui: Watched) -> None:
    """The name, the description and a tool of the agent under test are the agent's own words, so they may be markup."""
    target = mock_target(f"Evil {EVIL}", "success", description=f"{EVIL} a helpful assistant")
    target["mock"]["tools"] = [*target["mock"]["tools"], EVIL]
    run_id = lab.start_run(target)
    assert lab.wait(run_id)["status"] == "completed"
    with httpx.Client(base_url=lab.base) as c:
        target_id = next(t["id"] for t in c.get("/targets").json() if t["name"].startswith("Evil "))
    page = ui.page
    screens = [
        ("#/", "name"),
        ("#/runs", "name"),
        (f"#/runs/{run_id}", "name"),
        (f"#/runs/{run_id}/results", None),
        (f"#/runs/{run_id}/findings", None),
        (f"#/runs/{run_id}/scorecard", None),
        (f"#/runs/{run_id}/traces", None),
        (f"#/runs/{run_id}/plan", "name"),
        ("#/targets", "name"),
        (f"#/targets/{target_id}", "name"),
        ("#/reports", None),
        ("#/compare", None),
    ]
    shown_as_text = 0
    for route, expects in screens:
        page.goto(f"{lab.base}/{route}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        only_text(page)
        if expects:
            expect(page.get_by_text("onerror", exact=False).first).to_be_visible()
            shown_as_text += 1
    assert shown_as_text >= 6, "the markup is on screen, as characters, where the agent's name is shown"

    page.goto(f"{lab.base}/#/runs/{run_id}/reports")
    expect(page.locator("iframe").first).to_be_visible(timeout=30_000)
    page.wait_for_timeout(500)
    only_text(page)
    assert page.frame_locator("iframe").first.locator("img[src='x']").count() == 0, (
        "nor in the report, which escapes it"
    )
    page.get_by_role("tab", name="Markdown").click()
    page.wait_for_timeout(300)
    only_text(page)


# ====================================================================================================== reports
@needs_browser
def test_a_report_is_shown_in_a_sandboxed_frame_from_a_signed_link_and_a_new_version_can_be_made(
    lab: Lab, ui: Watched
) -> None:
    page = ui.page
    page.goto(f"{lab.base}/#/runs/{lab.runs['flawed']}/reports")
    frame = page.locator("iframe").first
    expect(frame).to_be_visible(timeout=30_000)
    sandbox = frame.get_attribute("sandbox") or ""
    assert "allow-scripts" in sandbox, "the interactive report needs its own scripts"
    assert "allow-same-origin" not in sandbox, "the report must not be able to reach the interface or its token"
    src = frame.get_attribute("src") or ""
    assert "/view/" in src and "token=" not in src and "Authorization" not in src
    # the report itself loads (the frame would be blank, and the page would log a policy violation, if it did not)
    inner = page.frame_locator("iframe").first
    expect(inner.locator("body")).not_to_be_empty()

    page.get_by_role("tab", name="Markdown").click()
    expect(page.get_by_text(re.compile("Executive summary|Scorecard", re.I)).first).to_be_visible()
    page.get_by_role("tab", name="JSON").click()
    expect(page.get_by_role("group", name="Structured data").first).to_be_visible()

    page.get_by_role("button", name="Generate report").click()
    expect(page.get_by_text("Version 2").first).to_be_visible(timeout=60_000)


@needs_browser
def test_two_runs_can_be_compared_and_the_regression_is_named(lab: Lab, ui: Watched) -> None:
    page = ui.page
    page.goto(lab.base + "/#/compare")
    expect(page.get_by_text("Choose two runs")).to_be_visible()
    page.locator("#cmp-a").select_option(value=lab.runs["clean"])
    page.locator("#cmp-b").select_option(value=lab.runs["flawed"])
    expect(page.get_by_text("Overall score").first).to_be_visible(timeout=30_000)
    expect(page.get_by_text(re.compile("New failure|regress", re.I)).first).to_be_visible()


# ====================================================================================================== credentials
@needs_browser
def test_a_credential_is_written_once_and_never_shown_again(lab: Lab, ui: Watched) -> None:
    page = ui.page
    secret = "demo-" + "token-" + "0123456789abcdef"
    replacement = "demo-" + "token-" + "fedcba9876543210"
    page.goto(lab.base + "/#/credentials")
    page.get_by_role("button", name="Add a credential").first.click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name", exact=True).fill("staging-api")
    dialog.get_by_label("Hosts it may be sent to").fill("staging.example.com")
    token = dialog.get_by_label("Token")
    assert token.get_attribute("type") == "password"
    token.fill(secret)
    dialog.get_by_role("button", name="Store credential").click()
    expect(page.get_by_text("staging-api").first).to_be_visible()
    for text in (secret, replacement):
        assert text not in page.content(), "a stored secret must not be in the page"
    assert secret not in str(stored(page))
    with httpx.Client(base_url=lab.base) as c:
        listed = c.get("/credentials").text
        assert "staging-api" in listed and secret not in listed, "the server never returns what it stored"

    page.get_by_role("button", name="Replace values").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("token").fill(replacement)
    dialog.get_by_role("button", name="Store the new values").click()
    expect(page.get_by_text("New values stored").first).to_be_visible()
    assert replacement not in page.content()

    page.get_by_role("button", name="Delete").click()
    page.get_by_role("button", name="Delete credential").click()
    expect(page.get_by_text("No credential stored")).to_be_visible()
    with httpx.Client(base_url=lab.base) as c:
        assert "staging-api" not in c.get("/credentials").text


# ====================================================================================================== stopping a run
@needs_browser
def test_a_run_can_be_stopped_from_the_interface_and_stops_safely(lab: Lab, ui: Watched) -> None:
    page = ui.page
    run_id = lab.start_run(
        mock_target("Slow agent", "success", "slow"),
        options={"intensity": "quick", "second_wave": False},
        overrides={"max_parallel": 1},
    )
    page.goto(f"{lab.base}/#/runs/{run_id}")
    expect(page.get_by_role("button", name="Stop run")).to_be_visible()
    page.wait_for_timeout(1200)  # a few tests have run
    page.get_by_role("button", name="Stop run").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label(re.compile("Reason")).fill("enough")
    dialog.get_by_role("button", name="Stop run").click()
    expect(page.get_by_text("This run was cancelled")).to_be_visible(timeout=60_000)
    final = lab.wait(run_id)
    assert final["status"] == "cancelled"
    assert final["totals"].get("cancel_reason") == "enough"


# ====================================================================================================== other projects
@needs_browser
def test_a_second_project_can_be_made_and_chosen_and_is_remembered(lab: Lab, ui: Watched) -> None:
    page = ui.page
    page.goto(lab.base + "/#/")
    page.locator("#project-switcher").select_option(label="New project…")
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name", exact=True).fill("second")
    dialog.get_by_role("button", name=re.compile("Create")).click()
    expect(page.locator("#project-switcher")).to_have_value("second")
    assert stored(page)["local"].get("agentlab.project") == "second"
    page.reload()
    expect(page.locator("#project-switcher")).to_have_value("second")
    expect(page.get_by_text(FLAWED_AGENT)).to_have_count(0)  # that project has nothing in it
    page.locator("#project-switcher").select_option("default")
    expect(page.get_by_text(FLAWED_AGENT).first).to_be_visible()


# ====================================================================================================== small screens
@needs_browser
def test_no_screen_scrolls_sideways_on_a_phone(lab: Lab, browser: Browser) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844})
    watched = Watched(context)
    run = lab.runs["flawed"]
    routes = [
        "/",
        "/new",
        "/targets",
        "/runs",
        "/reports",
        "/compare",
        "/skills",
        "/providers",
        "/credentials",
        "/settings",
        f"/runs/{run}",
        f"/runs/{run}/results",
        f"/runs/{run}/findings",
        f"/runs/{run}/scorecard",
        f"/runs/{run}/traces",
        f"/runs/{run}/reports",
        f"/runs/{run}/plan",
    ]
    try:
        wide: dict[str, int] = {}
        for route in routes:
            watched.page.goto(f"{lab.base}/#{route}")
            watched.page.wait_for_load_state("networkidle")
            overflow = watched.page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            if overflow > 4:
                wide[route] = overflow
        assert not wide, f"these screens scroll sideways on a 390 px wide screen: {wide}"
    finally:
        context.close()
    assert not watched.problems, "\n".join(watched.problems)


# ====================================================================================================== signing in
@needs_browser
def test_a_server_that_wants_a_token_asks_for_it_and_the_token_stays_in_the_tab(
    protected: Lab, browser: Browser
) -> None:
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    watched = Watched(context, allow_status=(401,))
    page = watched.page
    try:
        page.goto(protected.base + "/#/")
        expect(page.get_by_role("heading", name="Sign in to AgentLab")).to_be_visible()
        assert not [url for url, _ in watched.requests if url.endswith("/projects")], (
            "nothing protected is asked for first"
        )

        wrong = "wrong-" + "token-" + "y" * 20
        page.get_by_label("API token").fill(wrong)
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_text("The server did not accept that token.")).to_be_visible()
        assert wrong not in str(stored(page)) and wrong not in page.url

        page.get_by_label("API token").fill(TOKEN)
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_role("heading", name="Dashboard", level=1)).to_be_visible()

        # the token is in this tab's session storage and nowhere else: not the URL, not local storage, not the page
        keep = stored(page)
        assert keep["session"].get("agentlab.token") == TOKEN
        assert TOKEN not in str(keep["local"]) and TOKEN not in page.url and TOKEN not in page.content()
        # and it is sent only to the server that asked for it
        origin = protected.base
        sent_to = {url for url, headers in watched.requests if "authorization" in {k.lower() for k in headers}}
        assert sent_to and all(url.startswith(origin) for url in sent_to), sent_to

        # a reload in the same tab stays signed in; another tab (another session) asks again
        page.reload()
        expect(page.get_by_role("heading", name="Dashboard", level=1)).to_be_visible()
        other = context.new_page()
        other.goto(protected.base + "/#/")
        expect(other.get_by_role("heading", name="Sign in to AgentLab")).to_be_visible()
        other.close()

        # signing out forgets the token
        page.get_by_role("button", name=re.compile("Sign out", re.I)).click()
        expect(page.get_by_role("heading", name="Sign in to AgentLab")).to_be_visible()
        assert "agentlab.token" not in stored(page)["session"]
    finally:
        context.close()
    assert not watched.problems, "\n".join(watched.problems)

    # the server never wrote the token down
    time.sleep(0.1)
    assert TOKEN not in protected.proc.output
