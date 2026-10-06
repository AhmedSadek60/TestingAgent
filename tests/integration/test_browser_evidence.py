"""What a browser test leaves behind: screenshots, a Playwright trace, the action list and the site's own record, all
reachable from the report. Runs the real orchestrator against the browser fixture (marker ``browser``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlab.core.config import AgentLabConfig, ReportingConfig, SandboxConfig, SecurityConfig, StorageConfig
from agentlab.core.enums import TestStatus
from agentlab.core.models import TargetSpec
from agentlab.fixtures import BrowserAgentFixture
from agentlab.orchestrator import RunOptions, TestOrchestratorAgent
from agentlab.reporting.build import build_report
from agentlab.reporting.material import load_material
from agentlab.services import Services
from tests.support.browser import browser_ok

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(not browser_ok(), reason="Playwright with Chromium is not available"),
]


def services(root: Path) -> Services:
    cfg = AgentLabConfig(
        storage=StorageConfig(
            database_url=f"sqlite:///{root}/lab.db",
            artifacts_dir=str(root / "artifacts"),
            secrets_file=str(root / "s.enc"),
        ),
        security=SecurityConfig(sandbox=SandboxConfig(provider="disabled")),
        reporting=ReportingConfig(formats=[]),
    )
    return Services.create(cfg, base_dir=root)


@pytest.fixture
async def run(tmp_path: Path):  # noqa: ANN201
    sv = services(tmp_path)
    with BrowserAgentFixture.build("correct").deployed() as target:
        out = await TestOrchestratorAgent(sv).run(
            TargetSpec(**target),
            RunOptions(intensity="standard", second_wave=False, only_tests=["UI-ROUNDTRIP-*", "BROW-ADD-TO-CART-*"]),
        )
    yield sv, out
    await sv.aclose()


async def test_both_browser_tests_ran_and_passed(run) -> None:  # noqa: ANN001
    _, out = run
    by_id = {r.test_id: r for r in out.results}
    assert set(by_id) == {"UI-ROUNDTRIP-001", "BROW-ADD-TO-CART-001"}
    assert all(r.status == TestStatus.PASSED for r in by_id.values()), {k: v.status for k, v in by_id.items()}


async def test_the_ui_test_stored_its_actions_screenshots_and_trace(run) -> None:  # noqa: ANN001
    sv, out = run
    rows = {r["test_key"]: r for r in sv.store.list_browser_sessions(out.run_id)}
    ui = rows["UI-ROUNDTRIP-001"]
    assert ui["browser"] == "chromium" and ui["trace_artifact_id"]
    assert [a["action"] for a in ui["actions"]][:3] == ["goto", "chat", "screenshot"]
    assert all(a["ok"] for a in ui["actions"])
    assert len(ui["screenshot_artifact_ids"]) >= 1
    shot = sv.artifacts.get(ui["screenshot_artifact_ids"][0])
    assert shot[:8] == b"\x89PNG\r\n\x1a\n", "the screenshot is a real PNG"
    trace = sv.artifacts.get(ui["trace_artifact_id"])
    assert trace[:2] == b"PK", "the Playwright trace is a zip archive"


async def test_the_site_test_recorded_what_the_site_saw(run) -> None:  # noqa: ANN001
    sv, out = run
    rows = {r["test_key"]: r for r in sv.store.list_browser_sessions(out.run_id)}
    site = rows["BROW-ADD-TO-CART-001"]
    assert site["meta"]["observer"] == "local_site" and site["meta"]["site"]["cart"] == ["blue mug"]
    paths = [(a["action"], a["target"]) for a in site["actions"]]
    assert ("POST", "/cart/add") in paths and ("GET", "/shop") in paths
    assert all("seen by the test site" in a["detail"] for a in site["actions"])


async def test_the_report_carries_the_browser_evidence(run) -> None:  # noqa: ANN001
    sv, out = run
    report = build_report(sv, load_material(sv, out.run_id))
    sessions = {s.test_id: s for s in report.evidence.browser}
    assert set(sessions) == {"UI-ROUNDTRIP-001", "BROW-ADD-TO-CART-001"}
    ui = sessions["UI-ROUNDTRIP-001"]
    assert ui.trace and ui.screenshots and [a.action for a in ui.actions][:2] == ["goto", "chat"]
    kinds = {i.kind for i in report.evidence.items}
    assert {"screenshot", "browser_trace", "site_state"} <= kinds
