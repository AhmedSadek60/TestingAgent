"""Reports built from real stored runs (spec sections 24, 29, 30, 49 and 59).

Three runs against the deterministic MockAgent feed every test here: a clean one, a flawed one, and one whose target
name, test names and prompts are hostile (markup, script tags, link syntax, secret-shaped strings). Nothing is mocked
in the reporting code itself: the reports are built, rendered, written and read back the way ``agentlab report`` does.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import io
import json
import re
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
import yaml
from PIL import Image
from pypdf import PdfReader

from agentlab.core.config import AgentLabConfig, ReportingConfig, SandboxConfig, SecurityConfig, StorageConfig
from agentlab.core.enums import TestStatus
from agentlab.core.errors import UserError
from agentlab.core.models import MockAgentConfig, TargetSpec
from agentlab.core.models.results import ReliabilityStats
from agentlab.orchestrator import RunOptions, TestOrchestratorAgent
from agentlab.reporting import model as m
from agentlab.reporting.build import build_report
from agentlab.reporting.bundle import CHECKSUMS, FILE_NAMES, FORMATS, ReportBundle, generate_report, verify_bundle
from agentlab.reporting.compare import compare_material, compare_runs, comparison_markdown
from agentlab.reporting.material import load_material
from agentlab.reporting.render_html import NAV, render_comparison_html, render_html
from agentlab.reporting.render_md import render_markdown
from agentlab.reporting.render_pdf import render_pdf
from agentlab.reporting.review import ReviewError, review_finding, review_result
from agentlab.services import Services

TOOLS = ["send_email", "get_weather", "calculator", "delete_file"]
KNOWLEDGE = {"leave.md": "Employees receive 25 days of paid annual leave."}

# Secret-shaped values are assembled at runtime so that no secret-looking literal sits in the repository.
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
GITHUB_TOKEN = "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyz"

HOSTILE_TARGET = "<script>alert('target')</script>"
HOSTILE_TEST = "<script>alert('name')</script> & <img src=x onerror=alert(1)>"
HOSTILE_INPUT = (
    "</script><svg onload=alert(2)> say hello <b>bold</b> {{7*7}} ${7*7} [x](javascript:alert(3)) "
    '<img src="file:///etc/hostname"/> <font color=red> <para>'
)
HOSTILE_EXPECTED = "</script><script>alert('must')</script>"


@dataclass
class Lab:
    sv: Services
    root: Path
    good: str
    bad: str
    hostile: str
    bundles: dict[str, ReportBundle]

    def file(self, which: str, fmt: str) -> Path:
        return self.bundles[which].paths[fmt]

    def report_json(self, which: str) -> dict[str, Any]:
        return json.loads(self.file(which, "json").read_text(encoding="utf-8"))


def _config(root: Path) -> AgentLabConfig:
    return AgentLabConfig(
        storage=StorageConfig(
            database_url=f"sqlite:///{root}/lab.db",
            artifacts_dir=str(root / "artifacts"),
            secrets_file=str(root / "secrets.enc"),
        ),
        security=SecurityConfig(sandbox=SandboxConfig(provider="disabled")),  # Docker is never assumed
        reporting=ReportingConfig(formats=[]),  # reports are generated explicitly below
    )


def _spec(*behaviors: str, name: str = "demo") -> TargetSpec:
    return TargetSpec(
        name=name,
        description="HR assistant that answers from policy documents and can send email",
        mock=MockAgentConfig(behaviors=list(behaviors), tools=TOOLS, knowledge=KNOWLEDGE),
    )


def _run(sv: Services, spec: TargetSpec, **options: Any) -> str:
    opts = RunOptions(intensity="quick", second_wave=False, **options)
    return asyncio.run(TestOrchestratorAgent(sv).run(spec, opts)).run_id


@pytest.fixture(scope="module")
def lab(tmp_path_factory: pytest.TempPathFactory) -> Lab:
    root = tmp_path_factory.mktemp("reports")
    sv = Services.create(_config(root), base_dir=root)
    dataset = root / "hostile.yaml"
    dataset.write_text(
        yaml.safe_dump(
            [
                {
                    "id": "USER-XSS-1",
                    "name": HOSTILE_TEST,
                    "input": HOSTILE_INPUT,
                    "must_contain": [HOSTILE_EXPECTED],
                    "severity_on_failure": "high",
                },
                {
                    "id": "USER-SECRET-1",
                    "name": "A prompt that carries secret-shaped values",
                    "input": f"My key is {AWS_KEY} and my token is {GITHUB_TOKEN}",
                    "must_contain": [AWS_KEY],
                    "severity_on_failure": "low",
                },
            ]
        ),
        encoding="utf-8",
    )
    good = _run(sv, _spec("success"))
    bad = _run(sv, _spec("unsafe_behavior"))
    hostile = _run(
        sv,
        TargetSpec(
            name=HOSTILE_TARGET,
            description="Handles </script><img src=x onerror=alert(4)> things",
            mock=MockAgentConfig(behaviors=["unsafe_behavior"], tools=TOOLS, knowledge=KNOWLEDGE),
        ),
        user_test_files=[dataset],
    )
    bundles = {
        name: generate_report(sv, run_id, formats="all", output=root / "bundles" / name)
        for name, run_id in (("good", good), ("bad", bad), ("hostile", hostile))
    }
    yield Lab(sv, root, good, bad, hostile, bundles)
    sv.store.db.dispose()


# ======================================================================================================== helpers
class HtmlAudit(HTMLParser):
    """What a browser would find in a report: every tag, attribute and script, and the visible text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: Counter[str] = Counter()
        self.attrs: list[tuple[str, str, str]] = []
        self.scripts: list[tuple[dict[str, str | None], str]] = []
        self.ids: set[str] = set()
        self.meta: list[dict[str, str | None]] = []
        self.text: list[str] = []
        self._script: tuple[dict[str, str | None], list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag] += 1
        attr = dict(attrs)
        for name, value in attrs:
            self.attrs.append((tag, name, value or ""))
        if "id" in attr and attr["id"]:
            self.ids.add(attr["id"])
        if tag == "meta":
            self.meta.append(attr)
        if tag == "script":
            self._script = (attr, [])

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.scripts.append((self._script[0], "".join(self._script[1])))
            self._script = None

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script[1].append(data)
        else:
            self.text.append(data)


def audit(path: Path) -> HtmlAudit:
    parser = HtmlAudit()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def unfenced(markdown: str) -> str:
    """The Markdown outside code fences and code spans, where a viewer interprets what it finds."""
    kept: list[str] = []
    fence: str | None = None
    for line in markdown.split("\n"):
        opener = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            if opener and opener.group(1)[0] == fence[0] and len(opener.group(1)) >= len(fence):
                fence = None
            continue
        if opener:
            fence = opener.group(1)
            continue
        kept.append(re.sub(r"(`+)(?!`).+?(?<!`)\1(?!`)", "", line))
    return "\n".join(kept)


def pdf_text(path_or_bytes: Path | bytes) -> str:
    data = path_or_bytes.read_bytes() if isinstance(path_or_bytes, Path) else path_or_bytes
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


# ========================================================================================== completeness and form
def test_a_report_has_all_27_sections_in_every_format(lab: Lab) -> None:
    data = lab.report_json("bad")
    assert data["schema"] == "agentlab.report" and data["report_version"] == 1
    for key in (
        "executive", "scorecard", "risk", "target", "architecture", "classification", "capabilities", "environment",
        "methodology", "inventory", "results", "failed_tests", "security", "findings", "reliability", "performance",
        "cost", "regression", "evidence", "recommendations", "appendix", "versioning", "reviews",
    ):  # fmt: skip
        assert key in data, f"report.json has no '{key}'"

    markdown = lab.file("bad", "md").read_text(encoding="utf-8")
    numbered = [int(n) for n in re.findall(r"^## (\d+)\. ", markdown, flags=re.M)]
    assert numbered == list(range(1, 28)), "the Markdown report must carry sections 1 to 27, in order"

    page = audit(lab.file("bad", "html"))
    expected = {anchor for anchor, _ in NAV if anchor != "domain-other"}
    assert expected <= page.ids, f"HTML sections missing: {sorted(expected - page.ids)}"

    pdf = PdfReader(str(lab.file("bad", "pdf")))
    outline = [o.title for o in pdf.outline if not isinstance(o, list)]
    assert len(pdf.pages) > 5 and any("Executive summary" in t for t in outline)
    assert sum(1 for t in outline if re.match(r"^\d+[. ]", t)) >= 20, "the PDF outline lists the numbered sections"


def test_the_four_formats_are_renderings_of_the_same_data(lab: Lab) -> None:
    data = lab.report_json("bad")
    overall = f"{data['scorecard']['overall']:.0f}"
    run_id = data["run"]["run_id"]
    markdown = lab.file("bad", "md").read_text(encoding="utf-8")
    page = audit(lab.file("bad", "html"))
    embedded = next(json.loads(body) for attrs, body in page.scripts if attrs.get("type") == "application/json")

    assert embedded == data, "the HTML carries the very data the JSON report holds"
    assert run_id in markdown and f"({overall}/100)" in markdown
    assert run_id in " ".join(page.text) and overall in " ".join(page.text)
    assert run_id in pdf_text(lab.file("bad", "pdf")).replace("\n", "")
    findings = data["findings"]
    assert findings and all(f["title"] in markdown for f in findings[:5])


def test_the_html_report_is_one_self_contained_inert_file(lab: Lab) -> None:
    path = lab.file("good", "html")
    page = audit(path)
    executable = [(a, body) for a, body in page.scripts if a.get("type") in (None, "text/javascript")]
    data_blocks = [(a, body) for a, body in page.scripts if a.get("type") == "application/json"]
    assert len(executable) == 1 and len(data_blocks) == 1 and len(page.scripts) == 2
    assert "src" not in executable[0][0], "the one script is inline, never fetched"
    for forbidden in ("iframe", "object", "embed", "form", "link", "base", "frame"):
        assert page.tags[forbidden] == 0, f"<{forbidden}> must not appear in a report"
    for tag, name, value in page.attrs:
        assert not name.startswith("on"), f"<{tag} {name}> is an inline event handler"
        if name in {"src", "href", "action", "formaction", "xlink:href"}:
            assert not re.match(r"(?i)\s*(https?:|//|javascript:|data:text|ftp:)", value), f"<{tag} {name}={value!r}>"
    csp = next(a["content"] for a in page.meta if (a.get("http-equiv") or "").lower() == "content-security-policy")
    assert csp is not None and "default-src 'none'" in csp
    digest = base64.b64encode(hashlib.sha256(executable[0][1].encode("utf-8")).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in csp, "the policy allows exactly the one script that ships"
    assert "connect-src" not in csp and "http" not in csp.replace("http-equiv", "")


def test_blocked_tests_are_reported_as_blocked_never_as_failed(lab: Lab) -> None:
    data = lab.report_json("good")
    blocked = data["blocked_tests"]
    assert blocked, "the mock run has no judge, so judged tests are BLOCKED"
    assert not {b["test_id"] for b in blocked} & {f["test_id"] for f in data["failed_tests"]}
    rows = {r["test_id"]: r for r in data["results"]}
    assert all(rows[b["test_id"]]["status"] == "blocked" for b in blocked)
    assert all(b["blocked_reason"] for b in blocked), "a BLOCKED test says what was missing"
    markdown = lab.file("good", "md").read_text(encoding="utf-8")
    assert "BLOCKED" in markdown and "do not count as passes" in markdown
    first = blocked[0]["test_id"]
    assert not re.search(rf"\| {re.escape(first)} \|[^\n]*\| failed \|", markdown)


def test_a_flawed_agent_is_never_given_an_unqualified_good_grade(lab: Lab) -> None:
    data = lab.report_json("bad")
    sc = data["scorecard"]
    assert sc["security_cap_applied"] and sc["overall"] <= 65
    assert "capped by security" in sc["grade"]
    assert data["security"]["posture"] == "vulnerabilities_observed"
    summary = " ".join(p["text"] for p in data["executive"]["points"]) + data["executive"]["headline"]
    assert "leak" in summary.lower() or "vulnerab" in summary.lower() or "security" in summary.lower()


def test_findings_follow_the_section_30_shape(lab: Lab) -> None:
    findings = lab.report_json("bad")["findings"]
    assert findings
    for f in findings:
        for key in (
            "title", "evidence", "expected", "observed", "impact", "severity", "confidence", "reproduction",
            "recommendation",
        ):  # fmt: skip
            assert f.get(key) not in (None, "", []), f"finding {f.get('test_id')} has no {key}"
        assert "is bad" not in f["title"].lower() and "agent is bad" not in f["impact"].lower()
    assert any(f["root_cause"] for f in findings), "root causes are classified"


# =============================================================================================== hostile content
def test_hostile_text_is_inert_in_the_html_report(lab: Lab) -> None:
    path = lab.file("hostile", "html")
    raw = path.read_text(encoding="utf-8")
    page = audit(path)
    executable = [b for a, b in page.scripts if a.get("type") in (None, "text/javascript")]
    assert len(page.scripts) == 2 and len(executable) == 1
    assert "alert(" not in executable[0] and "hostile" not in executable[0]
    assert raw.count("</script") == 2, "no string can close the data block early (angle brackets are escaped in JSON)"
    for tag, name, value in page.attrs:
        assert not name.startswith("on"), f"<{tag} {name}> came from hostile text"  # an unescaped quote would add one
        if name in {"src", "href", "action", "formaction", "xlink:href", "style"}:
            assert "alert(" not in value and "javascript:" not in value.lower(), (tag, name, value)
    assert any(n == "data-text" and "alert(" in v for _t, n, v in page.attrs), "hostile text sits in attributes, quoted"
    assert page.tags["img"] == 0 or all(
        a.startswith("data:image/png") for t, n, a in page.attrs if t == "img" and n == "src"
    )
    assert page.tags["svg"] >= 1  # the report's own charts: hostile text never adds one that is not drawn by us
    shown = " ".join(page.text)
    assert HOSTILE_TEST in shown, "the hostile name is displayed as text, exactly as written"
    assert HOSTILE_TARGET in shown
    embedded = next(json.loads(b) for a, b in page.scripts if a.get("type") == "application/json")
    assert any(r["name"] == HOSTILE_TEST for r in embedded["results"]), "and survives intact in the data block"


def test_hostile_text_cannot_become_markup_in_the_markdown_report(lab: Lab) -> None:
    markdown = lab.file("hostile", "md").read_text(encoding="utf-8")
    interpreted = unfenced(markdown)
    for needle in ("<script", "<img", "<svg", "<font", "<para", "](javascript:"):
        assert needle not in interpreted, f"{needle!r} would be interpreted by a Markdown viewer"
    assert "&lt;script>alert('name')" in interpreted, "the text is still there, defused rather than dropped"


def test_hostile_text_is_printed_literally_in_the_pdf(lab: Lab) -> None:
    text = pdf_text(lab.file("hostile", "pdf"))
    flat = re.sub(r"\s+", " ", text)
    assert "alert('name')" in flat and "<script>" in flat, "markup in a name is drawn, never interpreted"
    assert "file:///etc/hostname" in flat, "an <img> in a prompt is text, not a request to load a file"


def test_secret_shaped_values_never_reach_a_report(lab: Lab) -> None:
    for fmt in ("json", "md", "html"):
        text = lab.file("hostile", fmt).read_text(encoding="utf-8")
        assert AWS_KEY not in text and GITHUB_TOKEN not in text, f"a secret reached report.{fmt}"
    pdf = re.sub(r"\s+", "", pdf_text(lab.file("hostile", "pdf")))
    assert AWS_KEY not in pdf and GITHUB_TOKEN not in pdf
    assert "[REDACTED" in lab.file("hostile", "json").read_text(encoding="utf-8"), "a mask shows where one was made"
    assert not verify_bundle(lab.bundles["hostile"].directory)  # type: ignore[arg-type]


def test_the_final_scrub_is_a_net_under_every_field_of_the_report(lab: Lab) -> None:
    from agentlab.reporting.bundle import scrub

    report = build_report(lab.sv, load_material(lab.sv, lab.good))
    report.executive.headline = f"A leaked key {AWS_KEY} and a token {GITHUB_TOKEN} in a field nobody sanitised"
    report.appendix.warnings.append(f"password = {'hunter' + '2-but-longer'}")
    clean, hits = scrub(report)
    dumped = json.dumps(clean.to_json_dict())
    assert AWS_KEY not in dumped and GITHUB_TOKEN not in dumped and "hunter2-but-longer" not in dumped
    assert hits.get("aws_access_key") == 1 and hits.get("github_token") == 1 and hits.get("password_assignment") == 1
    assert AWS_KEY in json.dumps(report.to_json_dict()), "scrubbing returns a copy and leaves its input alone"


# ========================================================================================= bundle and versioning
def test_every_report_is_a_new_version_and_a_bundle_is_never_overwritten(lab: Lab, tmp_path: Path) -> None:
    sv = lab.sv
    before = load_material(sv, lab.good).report_version
    first = generate_report(sv, lab.good, formats=["json", "md"])
    second = generate_report(sv, lab.good, formats=["json", "md"])
    assert first.report_version == before and second.report_version == before + 1
    assert first.directory and second.directory and first.directory != second.directory
    assert first.directory.name == f"v{before}" and second.directory.name == f"v{before + 1}"
    assert first.directory.parent.name == lab.good, "reports live under the run they describe"
    assert not verify_bundle(first.directory) and not verify_bundle(second.directory)
    held = {p.name: p.read_bytes() for p in first.directory.iterdir()}

    with pytest.raises(UserError, match="never overwritten"):
        generate_report(sv, lab.good, formats=["json"], output=first.directory)
    assert {p.name: p.read_bytes() for p in first.directory.iterdir()} == held, "the refusal changed nothing"
    assert sv.store.latest_report(lab.good)["report_version"] == before + 1
    assert set(sv.store.get_report(str(first.report_id))["formats"]) == {"json", "md"}
    assert sv.store.get_report(str(second.report_id))["report_version"] == before + 1


def test_checksums_catch_a_changed_missing_or_added_file(lab: Lab, tmp_path: Path) -> None:
    src = lab.bundles["good"].directory
    assert src is not None
    work = tmp_path / "copy"
    work.mkdir()
    for p in src.iterdir():
        (work / p.name).write_bytes(p.read_bytes())
    assert verify_bundle(work) == []
    index = json.loads((work / CHECKSUMS).read_text(encoding="utf-8"))
    assert index["schema"] == "agentlab.report-bundle" and index["run_id"] == lab.good
    assert set(index["files"]) == {*FILE_NAMES.values(), "run-manifest.json"}
    assert all(len(v["sha256"]) == 64 and v["bytes"] > 0 for v in index["files"].values())

    (work / "report.md").write_text("tampered\n", encoding="utf-8")
    (work / "report.pdf").unlink()
    (work / "extra.txt").write_text("added later\n", encoding="utf-8")
    problems = verify_bundle(work)
    assert any(p.startswith("report.md") and "checksum" in p for p in problems)
    assert any(p.startswith("report.pdf") and "missing" in p for p in problems)
    assert any(p.startswith("extra.txt") and "not in" in p for p in problems)
    (work / CHECKSUMS).write_text("not json", encoding="utf-8")
    assert "not a valid checksum index" in verify_bundle(work)[0]
    assert "nothing to verify" in verify_bundle(tmp_path / "nowhere")[0]


def test_the_report_says_what_produced_the_numbers(lab: Lab) -> None:
    v = lab.report_json("bad")["versioning"]  # spec section 49
    assert v["agentlab_version"] and v["python"] and v["platform"]
    assert v["target"] == "demo" and v["target_spec_hash"]
    assert v["test_suite"]["suite"] and v["test_suite"]["plan_hash"]
    assert len(v["skills"]) >= 10 and all(s["name"] and s["version"] for s in v["skills"])
    assert v["evaluation_profile"]["name"]
    assert v["environment_fingerprint"] and v["config_hash"] and v["timestamp"]
    assert v["judge_enabled"] is False, "no judge was configured and the report says so instead of implying one"
    markdown = lab.file("bad", "md").read_text(encoding="utf-8")
    assert "environment fingerprint" in markdown.lower() and v["environment_fingerprint"][:12] in markdown


def test_the_same_stored_run_gives_the_same_report_numbers(lab: Lab) -> None:
    material = load_material(lab.sv, lab.bad)
    a, b = build_report(lab.sv, material), build_report(lab.sv, material)
    assert render_markdown(a) == render_markdown(a)
    assert a.scorecard == b.scorecard and a.results == b.results and a.findings == b.findings
    assert a.executive == b.executive


def test_restricted_evidence_is_only_embedded_when_asked_for(lab: Lab, tmp_path: Path) -> None:
    sv = lab.sv
    run_id = _run(sv, _spec("success", name="evidence-demo"))
    ids = []
    for name, sensitivity, colour in (
        ("open.png", "normal", (10, 120, 200)),
        ("signed-in.png", "restricted", (200, 20, 20)),
    ):
        buf = io.BytesIO()
        Image.new("RGB", (6, 6), colour).save(buf, "PNG")  # distinct pixels: the store is content-addressed
        ref = sv.artifacts.put(
            buf.getvalue(),
            kind="screenshot",
            media_type="image/png",
            name=name,
            run_id=run_id,
            test_key="WEB-1",
            sensitivity=sensitivity,
            redact=False,
        )
        sv.store.register_artifact(ref)
        ids.append(ref.id)
    sv.store.save_browser_session(run_id, "WEB-1", "chromium", None, None, ids, [], {})

    def embedded(include: bool) -> int:
        bundle = generate_report(
            sv, run_id, formats=["html"], output=tmp_path / f"b{include}", include_sensitive=include
        )
        assert bundle.directory is not None
        return (bundle.directory / "report.html").read_text(encoding="utf-8").count("data:image/png;base64,")

    assert embedded(False) == 1, "the screenshot taken while signed in stays out of the report by default"
    assert embedded(True) == 2


# ============================================================================================ the PDF rendering
def test_the_pdf_is_valid_bookmarked_and_reproducible(lab: Lab) -> None:
    report = build_report(lab.sv, load_material(lab.sv, lab.bad))
    first, second = render_pdf(report), render_pdf(report)
    assert first == second, "the same data gives the same bytes (no clock, no random ids)"
    assert first[:5] == b"%PDF-"
    reader = PdfReader(io.BytesIO(first))
    assert len(reader.pages) > 5
    assert reader.metadata is not None and reader.metadata.title == report.title
    assert str(report.generated_at.year) in str(reader.metadata.creation_date)
    text = re.sub(r"\s+", " ", pdf_text(first))
    for needle in ("Executive summary", "Security findings", "Raw machine-readable results", str(report.run.run_id)):
        assert needle in text
    assert reader.outline, "a long report has a bookmark outline"


# ================================================================================================ the orchestrator
def test_the_report_phase_writes_the_configured_formats(tmp_path: Path) -> None:
    cfg = _config(tmp_path).model_copy(update={"reporting": ReportingConfig(formats=["json", "md"])})
    sv = Services.create(cfg, base_dir=tmp_path)
    try:
        out = asyncio.run(
            TestOrchestratorAgent(sv).run(_spec("success"), RunOptions(intensity="quick", second_wave=False))
        )
        phase = next(p for p in out.phases if p.phase.value == "report_generation")
        assert phase.status == "completed", phase
        assert out.report is not None and set(out.report.paths) == {"json", "md"}
        assert all(p.is_file() for p in out.report.paths.values())
        assert not verify_bundle(out.report.directory)  # type: ignore[arg-type]
        assert sv.store.latest_report(out.run_id)["report_version"] == 1
    finally:
        sv.store.db.dispose()


def test_a_run_that_tested_nothing_still_gets_an_honest_report(tmp_path: Path) -> None:
    from agentlab.core.models import ApiConfig

    sv = Services.create(_config(tmp_path), base_dir=tmp_path)
    try:
        spec = TargetSpec(name="down", description="an api that is down", api=ApiConfig(url="http://127.0.0.1:9/chat"))
        run_id = _run(sv, spec, suite="discovery")
        bundle = generate_report(sv, run_id, formats="all", output=tmp_path / "down")
        data = json.loads(bundle.paths["json"].read_text(encoding="utf-8"))
        assert data["scorecard"]["overall"] is None, "nothing ran: there is no score to show"
        text = bundle.paths["md"].read_text(encoding="utf-8").lower()
        assert "not tested" in text or "no test" in text or "could not be tested" in text
        assert not verify_bundle(bundle.directory)  # type: ignore[arg-type]
        assert len(PdfReader(str(bundle.paths["pdf"])).pages) >= 1
    finally:
        sv.store.db.dispose()


# ================================================================================================== human review
def test_a_review_sits_beside_the_original_and_never_replaces_it(lab: Lab, tmp_path: Path) -> None:
    sv = lab.sv
    run_id = _run(sv, _spec("unsafe_behavior", name="review-demo"))
    before = {r.test_id: (r.status, r.score, r.severity) for r in sv.store.list_results(run_id)}
    machine = generate_report(sv, run_id, formats=["json"], output=tmp_path / "before")
    original_card = json.loads(machine.paths["json"].read_text(encoding="utf-8"))["scorecard"]
    failed = [t for t, (status, _s, _v) in before.items() if status == TestStatus.FAILED]
    assert len(failed) >= 3

    for test_id in failed:
        review_result(
            sv, run_id, test_id, decision="false_positive", reviewer="Reviewer One", reason="the check was too strict"
        )
    review_result(
        sv, run_id, failed[0], decision="override_score", reviewer="Reviewer Two", reason="partial credit", score=0.5
    )
    after = {r.test_id: (r.status, r.score, r.severity) for r in sv.store.list_results(run_id)}
    assert after == before, "the original evaluation is never touched"

    report = generate_report(sv, run_id, formats=["json", "md", "html"], output=tmp_path / "after")
    data = json.loads(report.paths["json"].read_text(encoding="utf-8"))
    assert data["scorecard"] == original_card, "the machine scorecard is unchanged"
    reviewed = data["reviewed_scorecard"]
    assert reviewed is not None and reviewed["raw_overall"] > original_card["raw_overall"]
    assert reviewed["security_cap_applied"], "findings are reviewed on their own: the open ones still cap the score"
    assert "human review" in " ".join(reviewed["notes"]).lower()

    for finding in {f.test_id: f for f in sv.store.list_findings(run_id)}:
        review_finding(
            sv, run_id, finding, decision="false_positive", reviewer="Reviewer One", reason="checked by hand"
        )
    cleared = json.loads(
        generate_report(sv, run_id, formats=["json"], output=tmp_path / "cleared").paths["json"].read_text("utf-8")
    )
    assert cleared["scorecard"] == original_card and not cleared["reviewed_scorecard"]["security_cap_applied"]
    assert cleared["reviewed_scorecard"]["overall"] > original_card["overall"]
    data = cleared  # the log below holds every review made so far
    assert data["reviewed"] is True and len(data["reviews"]) > len(failed) + 1
    one = next(r for r in data["reviews"] if r["decision"] == "false_positive")
    assert one["reviewer"] == "Reviewer One" and one["reason"] and one["created_at"]
    assert one["original"]["status"] == "failed" and one["reviewed"]["status"] == "passed"
    assert one["subject"] in failed, "the review log names the test, not an opaque id"
    final = generate_report(sv, run_id, formats=["md", "html"], output=tmp_path / "final")
    markdown = final.paths["md"].read_text(encoding="utf-8")
    assert "Human review log" in markdown and "Reviewer Two" in markdown
    assert "original results above are unchanged" in markdown
    assert "Human review log" in final.paths["html"].read_text(encoding="utf-8")
    shown = {r["test_id"]: r["status"] for r in data["results"]}
    assert all(shown[t] == "failed" for t in failed), "the results table still shows what the machine measured"


def test_reviews_are_validated_before_they_are_stored(lab: Lab) -> None:
    sv = lab.sv
    results = sv.store.list_results(lab.bad)
    failed = next(r for r in results if r.status == TestStatus.FAILED)
    passed = next(r for r in results if r.status == TestStatus.PASSED)
    count = len(sv.store.list_reviews(lab.bad))
    with pytest.raises(ReviewError, match="name of the reviewer"):
        review_result(sv, lab.bad, failed.test_id, decision="comment", reviewer=" ", comment="x")
    with pytest.raises(ReviewError, match="needs a reason"):
        review_result(sv, lab.bad, failed.test_id, decision="false_positive", reviewer="r")
    with pytest.raises(ReviewError, match="unknown review decision"):
        review_result(sv, lab.bad, failed.test_id, decision="looks-fine", reviewer="r")
    with pytest.raises(ReviewError, match="no result for test"):
        review_result(sv, lab.bad, "NOPE-001", decision="comment", reviewer="r", comment="x")
    with pytest.raises(ReviewError, match="applies to a failed test"):
        review_result(sv, lab.bad, passed.test_id, decision="false_positive", reviewer="r", reason="x")
    with pytest.raises(ReviewError, match="applies to a passed test"):
        review_result(sv, lab.bad, failed.test_id, decision="false_negative", reviewer="r", reason="x")
    with pytest.raises(ReviewError, match="score"):
        review_result(sv, lab.bad, failed.test_id, decision="override_score", reviewer="r", reason="x", score=1.5)
    with pytest.raises(ReviewError, match="unknown severity"):
        review_result(
            sv, lab.bad, failed.test_id, decision="change_severity", reviewer="r", reason="x", severity="huge"
        )
    with pytest.raises(ReviewError, match="applies to a test result"):
        review_finding(sv, lab.bad, failed.test_id, decision="override_score", reviewer="r", reason="x")
    assert len(sv.store.list_reviews(lab.bad)) == count, "an invalid review leaves no trace"


def test_a_finding_can_be_confirmed_downgraded_and_commented(tmp_path: Path) -> None:
    sv = Services.create(_config(tmp_path), base_dir=tmp_path)
    try:
        run_id = _run(sv, _spec("unsafe_behavior"))
        distinct = list({f.test_id: f for f in sv.store.list_findings(run_id) if f.severity.value != "low"}.values())
        assert len(distinct) >= 3
        a, b, c = (f.test_id for f in distinct[:3])
        review_finding(sv, run_id, a, decision="approve", reviewer="r", comment="confirmed by hand")
        review_finding(sv, run_id, b, decision="change_severity", reviewer="r", reason="needs a login", severity="low")
        review_finding(sv, run_id, c, decision="false_positive", reviewer="r", reason="expected behaviour")
        rows = {r["decision"]: r for r in sv.store.list_reviews(run_id)}
        assert rows["approve"]["reviewed"] == {"status": "confirmed"}
        assert rows["change_severity"]["reviewed"] == {"severity": "low"}
        assert rows["change_severity"]["original"]["severity"] in {"critical", "high", "medium"}
        data = build_report(sv, load_material(sv, run_id))
        by_test = {f.test_id: f for f in data.findings}
        assert by_test[b].reviews and by_test[b].effective_severity == "low"
        assert by_test[b].severity != "low", "the finding keeps the severity the machine assigned"
        assert by_test[c].status == "false_positive" and by_test[a].status == "confirmed"
        stored = {f.test_id: f for f in sv.store.list_findings(run_id)}
        assert stored[b].severity.value != "low" and stored[c].status in {"open", "confirmed"}
    finally:
        sv.store.db.dispose()


# ====================================================================================== regression comparison
def test_comparing_runs_reports_new_and_resolved_failures_and_flags_nothing_hidden(lab: Lab) -> None:
    better = compare_runs(lab.sv, lab.bad, lab.good)
    worse = compare_runs(lab.sv, lab.good, lab.bad)
    assert better.verdict == "improved" and worse.verdict == "regressed"
    assert better.counts.get("resolved", 0) == worse.counts.get("new_failure", 0) > 0
    assert better.score["overall_delta"] > 0 > worse.score["overall_delta"]
    assert worse.security["new_attacks_succeeded"] and worse.security["direction"] == "worse"
    for field in ("latency", "cost", "reliability", "categories", "findings"):
        assert getattr(worse, field), f"a comparison covers {field}"
    assert {d.test_id for d in worse.tests if d.kind == "new_failure"} == {
        d.test_id for d in better.tests if d.kind == "resolved"
    }
    text = comparison_markdown(worse, title=True)
    assert "New failures" in text and "Compatibility" in text
    page = render_comparison_html(worse)
    assert "New failures" in page and "<script" not in page.split("<body", 1)[1].split("<script", 1)[0]


def test_a_run_is_not_comparable_with_itself_and_says_so(lab: Lab) -> None:
    same = compare_runs(lab.sv, lab.good, lab.good)
    assert same.compatibility.verdict == "not_comparable" and same.verdict == "inconclusive"
    assert "same run" in same.compatibility.summary


def test_a_test_that_could_not_run_is_lost_coverage_not_a_regression(lab: Lab) -> None:
    a = load_material(lab.sv, lab.good)
    b = copy.deepcopy(a)
    b.run["id"] = "run-b"
    victim = next(r for r in b.results if r.status == TestStatus.PASSED)
    victim.status, victim.blocked_reason = TestStatus.BLOCKED, "the credential 'staff' was not available"
    comparison = compare_material(a, b)
    delta = next(d for d in comparison.tests if d.test_id == victim.test_id)
    assert delta.kind == "lost_coverage" and "credential" in delta.note
    assert comparison.counts.get("new_failure", 0) == 0
    assert comparison.verdict != "regressed", "a test that could not run is not a test that failed"
    reverse = compare_material(b, a)
    assert next(d for d in reverse.tests if d.test_id == victim.test_id).kind == "gained_coverage"


def test_a_flaky_flip_is_unstable_not_a_regression_or_a_fix(lab: Lab) -> None:
    a = load_material(lab.sv, lab.good)
    b = copy.deepcopy(a)
    b.run["id"] = "run-b"
    victim = next(r for r in b.results if r.status == TestStatus.PASSED)
    victim.status = TestStatus.FAILED
    victim.reliability = ReliabilityStats(
        repetitions=5, passes=3, pass_rate=0.6, flaky=True, deterministic_failure=False
    )
    comparison = compare_material(a, b)
    assert next(d for d in comparison.tests if d.test_id == victim.test_id).kind == "unstable"
    assert comparison.counts.get("new_failure", 0) == 0 and comparison.counts.get("unstable") == 1


def test_runs_scored_differently_are_flagged_and_their_scores_are_not_compared(lab: Lab) -> None:
    a = load_material(lab.sv, lab.bad)
    b = copy.deepcopy(a)
    b.run["id"] = "run-b"
    b.manifest["scoring_profile"] = {**b.manifest["scoring_profile"], "name": "stricter", "hash": "other"}
    comparison = compare_material(a, b)
    assert comparison.compatibility.verdict == "not_comparable" and comparison.verdict == "inconclusive"
    assert any(
        d.field == "scoring_profile" and d.impact == "blocks_comparison" for d in comparison.compatibility.differences
    )
    assert comparison.score["overall_comparable"] is False and "NOT comparable" in comparison.score["note"]
    assert "NOT comparable" in comparison_markdown(comparison)


def test_a_changed_skill_or_plan_is_a_caveat_and_a_changed_test_is_not_compared(lab: Lab) -> None:
    a = load_material(lab.sv, lab.bad)
    b = copy.deepcopy(a)
    b.run["id"] = "run-b"
    skill = b.manifest["skills"][0]
    skill["version"] = "99.0.0"
    b.manifest["plan"] = {**b.manifest["plan"], "hash": "changed"}
    plan_test = next(iter(b.planned().values()))
    plan_test.test.input = "A different question entirely"
    comparison = compare_material(a, b)
    fields = {d.field: d.impact for d in comparison.compatibility.differences}
    assert fields["plan.hash"] == "caveat" and fields[f"skills.{skill['name']}"] == "caveat"
    assert comparison.compatibility.verdict == "comparable_with_caveats"
    changed = [d for d in comparison.tests if d.kind == "definition_changed"]
    assert len(changed) >= 1 and comparison.compatibility.changed_definitions == len(changed)
    assert all(d.status_a and d.status_b for d in changed), "both outcomes are listed, neither is scored"
    assert "differ in content" in " ".join(comparison.compatibility.notes)


def test_a_report_made_with_a_baseline_carries_the_regression_section(lab: Lab, tmp_path: Path) -> None:
    bundle = generate_report(lab.sv, lab.bad, formats=["json", "md"], baseline=lab.good, output=tmp_path / "vs")
    data = json.loads(bundle.paths["json"].read_text(encoding="utf-8"))
    reg = data["regression"]
    assert reg is not None and reg["verdict"] == "regressed" and reg["run_a"]["run_id"] == lab.good
    markdown = bundle.paths["md"].read_text(encoding="utf-8")
    assert "## 22. Regression comparison" in markdown and "New failures" in markdown
    plain = json.loads(lab.file("good", "json").read_text(encoding="utf-8"))["regression"]
    assert plain is None, "no baseline, no comparison: it is never invented"


def test_format_names_are_validated() -> None:
    assert FORMATS == ("json", "md", "html", "pdf")
    from agentlab.reporting.bundle import normalise_formats

    assert normalise_formats(None) == list(FORMATS) == normalise_formats("all")
    assert normalise_formats(["HTML, markdown"]) == ["md", "html"]
    with pytest.raises(UserError, match="unknown report format 'docx'"):
        normalise_formats(["json", "docx"])
    assert render_html  # the renderers are importable without a run: nothing in them needs a service


def _unused(_: m.ReportData) -> None:  # keeps the model import honest: reports are typed data first
    return None
