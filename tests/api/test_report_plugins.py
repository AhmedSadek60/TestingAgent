"""A report format that a plug-in adds is a format of the REST API too: generated, listed, downloaded and served safely."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agentlab.reporting import model as m
from agentlab.reporting.renderers import REPORT_RENDERERS, RenderContext, ReportRenderer
from tests.support.api import running_api

TOKEN = "plugin-" + "token-" + "q" * 16


class XmlSummary(ReportRenderer):
    file_name = "report.summary.xml"
    media_type = "application/xml"

    def render(self, report: m.ReportData, context: RenderContext) -> bytes:
        return f'<run id="{report.run.run_id}" tests="{len(report.results)}"/>'.encode()


class PageRenderer(ReportRenderer):
    """A document a browser would run if it were served as a page of the API."""

    file_name = "report.page.html"
    media_type = "text/html"

    def render(self, report: m.ReportData, context: RenderContext) -> bytes:
        return b"<html><body><script>window.stolen = document.cookie</script>a page</body></html>"


@pytest.fixture
def formats() -> Iterator[None]:
    REPORT_RENDERERS.register("xml-summary", XmlSummary)
    REPORT_RENDERERS.register("page", PageRenderer)
    yield
    REPORT_RENDERERS.unregister("xml-summary")
    REPORT_RENDERERS.unregister("page")


async def test_a_plug_in_format_is_generated_listed_and_downloaded_over_the_api(formats: None, tmp_path: Path) -> None:
    async with running_api(tmp_path, token=TOKEN) as api:
        run = await api.run()
        made = await api.client.post(f"/test-runs/{run['id']}/reports", json={"formats": ["xml-summary", "json"]})
        assert made.status_code == 201, made.text
        report: dict[str, Any] = made.json()
        by_format = {f["format"]: f for f in report["formats"]}
        assert sorted(by_format) == ["json", "xml-summary"]
        assert by_format["xml-summary"]["media_type"] == "application/xml" and by_format["xml-summary"]["size"] > 0

        download = await api.client.get(by_format["xml-summary"]["url"])
        assert download.status_code == 200 and download.headers["content-type"].startswith("application/xml")
        assert ET.fromstring(download.content).attrib["id"] == run["id"]

        listed = (await api.client.get(f"/test-runs/{run['id']}/reports")).json()
        assert any(f["format"] == "xml-summary" for r in listed for f in r["formats"])

        exported = await api.client.post(f"/reports/{report['id']}/export", json={"format": "xml-summary"})
        assert exported.status_code == 200 and exported.json()["created_new_version"] is False


async def test_a_format_an_older_report_has_is_still_listed_after_its_plug_in_is_removed(tmp_path: Path) -> None:
    REPORT_RENDERERS.register("xml-summary", XmlSummary)
    try:
        async with running_api(tmp_path, token=TOKEN) as api:
            run = await api.run()
            made = (await api.client.post(f"/test-runs/{run['id']}/reports", json={"formats": ["xml-summary"]})).json()
            REPORT_RENDERERS.unregister("xml-summary")
            again = (await api.client.get(f"/reports/{made['id']}")).json()
            assert [f["format"] for f in again["formats"]] == ["xml-summary"], "the stored file does not vanish"
            assert again["formats"][0]["media_type"] == "application/xml", "its recorded media type is what is served"
            kept = await api.client.get(again["formats"][0]["url"])
            assert kept.status_code == 200 and kept.content.startswith(b"<run ")
    finally:
        REPORT_RENDERERS.unregister("xml-summary")


async def test_a_plug_ins_html_is_served_sandboxed_like_any_document_of_a_run(formats: None, tmp_path: Path) -> None:
    async with running_api(tmp_path, token=TOKEN) as api:
        run = await api.run()
        report = (await api.client.post(f"/test-runs/{run['id']}/reports", json={"formats": ["page"]})).json()
        page = await api.client.get(report["formats"][0]["url"])
        assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
        csp = page.headers["content-security-policy"]
        assert "sandbox" in csp and "default-src 'none'" in csp, "a plug-in cannot make a document that runs as the API"


async def test_a_format_nothing_provides_is_a_clear_422_with_the_names_that_exist(
    formats: None, tmp_path: Path
) -> None:
    async with running_api(tmp_path, token=TOKEN) as api:
        run = await api.run()
        bad = await api.client.post(f"/test-runs/{run['id']}/reports", json={"formats": ["docx"]})
        assert bad.status_code == 422
        message = bad.json()["error"]["message"]
        assert "unknown report format 'docx'" in message and "xml-summary" in message and "json" in message


async def test_the_schema_names_formats_as_plain_strings_so_a_plug_ins_format_is_not_rejected_by_a_client(
    tmp_path: Path,
) -> None:
    async with running_api(tmp_path, token=TOKEN) as api:
        schemas = (await api.client.get("/openapi.json")).json()["components"]["schemas"]
        assert schemas["ReportFileOut"]["properties"]["format"]["type"] == "string"
        assert schemas["ExportRequest"]["properties"]["format"]["type"] == "string"
        assert schemas["ReportCreate"]["properties"]["formats"]["items"]["type"] == "string"
