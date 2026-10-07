"""Documents are read from this machine only, one by one, and a file that cannot be read is a warning."""

from __future__ import annotations

from pathlib import Path

from agentlab.core.config import AgentLabConfig
from agentlab.core.models import TargetSpec
from agentlab.discovery.agent import TargetDiscoveryAgent


def read(spec: TargetSpec) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    docs = TargetDiscoveryAgent(AgentLabConfig())._documents(spec, warnings)
    return [d.name for d in docs], warnings


def test_a_url_is_not_downloaded_and_the_warning_says_what_to_do() -> None:
    names, warnings = read(
        TargetSpec(name="t", documents=["https://example.com/policy.pdf", "http://example.com/a.md"])
    )
    assert names == []
    assert warnings == [
        "https://example.com/policy.pdf: a URL is not downloaded; save the document and give its path",
        "http://example.com/a.md: a URL is not downloaded; save the document and give its path",
    ]


def test_a_folder_is_read_recursively_and_a_file_that_cannot_be_read_is_a_warning(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "leave.txt").write_text("Employees receive 25 days of annual leave.\n", encoding="utf-8")
    (tmp_path / "sub" / "returns.md").write_text("# Returns\n\nRefunds within 30 days.\n", encoding="utf-8")
    (tmp_path / "thing.xyz").write_bytes(b"opaque")
    names, warnings = read(TargetSpec(name="t", documents=[str(tmp_path), str(tmp_path / "missing.docx")]))
    assert sorted(names) == ["leave.txt", "returns.md", "thing.xyz"], "an unreadable format is listed, with its warning"
    assert any("thing.xyz" in w and "no parser" in w for w in warnings)
    assert any("missing.docx" in w and "not found" in w for w in warnings)


def test_at_most_two_hundred_files_of_a_folder_are_read(tmp_path: Path) -> None:
    for n in range(205):
        (tmp_path / f"d{n:03}.txt").write_text(f"Document number {n}.\n", encoding="utf-8")
    names, _ = read(TargetSpec(name="t", documents=[str(tmp_path)]))
    assert len(names) == 200
