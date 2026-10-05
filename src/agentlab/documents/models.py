"""Document analysis outputs. Every extracted item keeps its provenance (spec section 22)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agentlab.core.models.base import Model


class KnowledgeItem(Model):
    id: str
    document: str
    doc_sha256: str
    version: str  # first 12 hex chars of the document hash
    page: int | None = None
    section: str | None = None
    location: str = ""  # human-readable: "page 3, paragraph 2" / "lines 12-18" / "row 4"
    kind: str = "paragraph"  # paragraph | heading | table | code | list | row
    text: str
    text_sha256: str
    order: int = 0

    def cite(self) -> str:
        parts = [self.document]
        if self.page:
            parts.append(f"p.{self.page}")
        if self.section:
            parts.append(f"§ {self.section}")
        return " ".join(parts)


class TableData(Model):
    page: int | None = None
    section: str | None = None
    header: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    heuristic: bool = False


class Heading(Model):
    level: int
    text: str
    page: int | None = None


class AnalyzedDocument(Model):
    name: str
    media_type: str
    sha256: str
    version: str
    size: int
    pages: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    headings: list[Heading] = Field(default_factory=list)
    tables: list[TableData] = Field(default_factory=list)
    items: list[KnowledgeItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    injection_indicators: list[str] = Field(default_factory=list)
    hidden_content: list[str] = Field(default_factory=list)
    active_content: list[str] = Field(default_factory=list)
    unsupported: str | None = None
    text_chars: int = 0


class Fact(Model):
    """A checkable statement extracted from a document, with the value a correct answer must contain."""

    item_id: str
    source: str
    statement: str
    subject: str
    values: list[str]
    question: str


class Requirement(Model):
    item_id: str
    source: str
    text: str
    modality: str  # must | should | must_not | acceptance
