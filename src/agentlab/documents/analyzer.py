"""DocumentAnalyzer: text, tables, headings, metadata and *provenance* for every knowledge item.

Items are chunked so a RAG test can say exactly which document, page, section and location a fact
came from, plus a content hash and a version tag (first 12 hex chars of the document hash).
Safety observations (hidden text, active content, instruction-like text) are recorded on the
document; the content itself is always treated as untrusted data.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from agentlab.core.errors import ParserError, UserError
from agentlab.documents.models import AnalyzedDocument, Fact, KnowledgeItem, Requirement
from agentlab.documents.parsers import DOCUMENT_PARSERS, Block
from agentlab.security.untrusted import injection_indicators

MAX_DOC_BYTES = 50 * 1024 * 1024
CHUNK_TARGET = 700
MEDIA = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".html": "text/html",
    ".htm": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
VALUE = re.compile(
    r"(?:[$€£]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:%|percent|days?|weeks?|months?|years?|hours?|"
    r"minutes?|usd|eur|gbp|dollars?|euros?|mb|gb|kb)|\b(?:19|20)\d{2}-\d{2}-\d{2}\b|"
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?|\b\d{2,}\b)",
    re.I,
)
MODAL = re.compile(
    r"\b(must not|shall not|must|shall|should not|should|required to|is required|are required|"
    r"may not|is prohibited|are prohibited|never)\b",
    re.I,
)
STOP = set(
    [
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "for",
        "on",
        "at",
        "and",
        "or",
        "but",
        "is",
        "are",
        "was",
        "were",
        "be",
        "by",
        "with",
        "from",
        "as",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
    ]
)


def _hash(b: bytes | str) -> str:
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode()).hexdigest()


class DocumentAnalyzer:
    def analyze_bytes(self, data: bytes, name: str, media_type: str | None = None) -> AnalyzedDocument:
        if len(data) > MAX_DOC_BYTES:
            raise UserError(f"{name}: larger than {MAX_DOC_BYTES} bytes")
        ext = Path(name).suffix.lower()
        sha = _hash(data)
        doc = AnalyzedDocument(
            name=name,
            media_type=media_type or MEDIA.get(ext, "application/octet-stream"),
            sha256=sha,
            version=sha[:12],
            size=len(data),
        )
        if ext not in DOCUMENT_PARSERS.names():
            doc.unsupported = f"no parser for '{ext or 'unknown'}' files"
            doc.warnings.append(doc.unsupported)
            return doc
        try:
            parsed = DOCUMENT_PARSERS.get(ext)(data, name)
        except ParserError as exc:
            doc.unsupported = str(exc)
            doc.warnings.append(str(exc))
            return doc
        except Exception as exc:  # a malformed document must never crash the run
            doc.unsupported = f"{type(exc).__name__} while parsing: {exc}"
            doc.warnings.append(doc.unsupported)
            return doc
        doc.pages, doc.metadata, doc.headings, doc.tables = (
            parsed.pages,
            parsed.metadata,
            parsed.headings,
            parsed.tables,
        )
        doc.warnings += parsed.warnings
        doc.hidden_content, doc.active_content, doc.unsupported = parsed.hidden, parsed.active, parsed.unsupported
        doc.items = self._chunk(doc, parsed.blocks)
        doc.text_chars = sum(len(i.text) for i in doc.items)
        full = "\n".join(i.text for i in doc.items) + "\n" + "\n".join(parsed.hidden)
        doc.injection_indicators = injection_indicators(full)
        if doc.injection_indicators:
            doc.warnings.append(
                "contains instruction-like text aimed at AI systems (kept as data; will be used for "
                "indirect-prompt-injection tests, never obeyed)"
            )
        if doc.hidden_content:
            doc.warnings.append(f"{len(doc.hidden_content)} hidden/invisible content fragment(s) detected")
        if not doc.items and not doc.unsupported:
            doc.warnings.append("no text could be extracted")
        return doc

    def analyze_path(self, path: str | Path) -> AnalyzedDocument:
        p = Path(path)
        if not p.is_file():
            raise UserError(f"document not found: {p}")
        return self.analyze_bytes(p.read_bytes(), p.name)

    # ------------------------------------------------------------------ chunking
    @staticmethod
    def _chunk(doc: AnalyzedDocument, blocks: list[Block]) -> list[KnowledgeItem]:
        items: list[KnowledgeItem] = []
        order = 0

        def add(text: str, b: Block, kind: str, loc_suffix: str = "") -> None:
            nonlocal order
            order += 1
            items.append(
                KnowledgeItem(
                    id=f"{doc.version}:{order:04d}",
                    document=doc.name,
                    doc_sha256=doc.sha256,
                    version=doc.version,
                    page=b.page,
                    section=b.section,
                    location=(b.location + loc_suffix).strip(", "),
                    kind=kind,
                    text=text,
                    text_sha256=_hash(text)[:16],
                    order=order,
                )
            )

        for b in blocks:
            text = b.text.strip()
            if not text:
                continue
            if b.kind in {"heading", "table", "code", "row"} or len(text) <= CHUNK_TARGET * 1.3:
                add(text, b, b.kind)
                continue
            cur = ""
            part = 1
            for s in SENT.split(text):
                if cur and len(cur) + len(s) > CHUNK_TARGET:
                    add(cur.strip(), b, b.kind, f", part {part}")
                    cur, part = "", part + 1
                cur += s + " "
            if cur.strip():
                add(cur.strip(), b, b.kind, f", part {part}" if part > 1 else "")
        return items


# --------------------------------------------------------------------------------- fact + requirement extraction
def _subject(sentence: str, value: str) -> str:
    """A short topic label: the first informative words of the sentence (used for naming/dedup, not for wording)."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", sentence) if w.lower() not in STOP]
    return " ".join(words[:4])


def _cloze(sentence: str, value: str) -> str:
    masked = sentence.replace(value, "___", 1)
    return (
        f'According to the provided documents, complete this statement with the exact value from the source: "{masked}"'
    )


def extract_facts(doc: AnalyzedDocument, limit: int = 12) -> list[Fact]:
    """Checkable statements: sentences that carry a concrete value (number, amount, date, duration)."""
    facts: list[Fact] = []
    seen_subjects: set[str] = set()
    for item in doc.items:
        if item.kind in {"code", "heading"}:
            continue
        for s in SENT.split(item.text):
            s = s.strip()
            words = s.split()
            if not 5 <= len(words) <= 45:
                continue
            vals = [v.strip() for v in VALUE.findall(s) if v.strip() and not re.fullmatch(r"\d", v.strip())]
            vals = [v for v in vals if not re.fullmatch(r"(?:19|20)\d{2}", v)] or vals
            if not vals:
                continue
            subj = _subject(s, vals[0])
            if not subj or subj.lower() in seen_subjects:
                continue
            seen_subjects.add(subj.lower())
            facts.append(
                Fact(
                    item_id=item.id,
                    source=item.cite(),
                    statement=s,
                    subject=subj,
                    values=vals[:3],
                    question=_cloze(s, vals[0]),
                )
            )
            if len(facts) >= limit:
                return facts
    return facts


def extract_requirements(doc: AnalyzedDocument, limit: int = 20) -> list[Requirement]:
    """Normative statements (must/shall/should/never) and acceptance criteria usable as test oracles."""
    reqs: list[Requirement] = []
    for item in doc.items:
        for s in SENT.split(item.text):
            s = s.strip()
            if not 4 <= len(s.split()) <= 50:
                continue
            m = MODAL.search(s)
            ac = (
                re.match(r"^(given|when|then|acceptance)\b", s, re.I)
                or "acceptance criteria" in (item.section or "").lower()
            )
            if not (m or ac):
                continue
            mod = "acceptance" if ac and not m else m.group(1).lower().replace(" ", "_") if m else "must"
            mod = {
                "shall": "must",
                "shall_not": "must_not",
                "is_required": "must",
                "are_required": "must",
                "required_to": "must",
                "should_not": "must_not",
                "may_not": "must_not",
                "is_prohibited": "must_not",
                "are_prohibited": "must_not",
                "never": "must_not",
            }.get(mod, mod)
            reqs.append(Requirement(item_id=item.id, source=item.cite(), text=s, modality=mod))
            if len(reqs) >= limit:
                return reqs
    return reqs


def find_conflicts(docs: list[AnalyzedDocument], limit: int = 5) -> list[tuple[Fact, Fact]]:
    """Pairs of facts about the same subject with different values (conflicting or outdated documents)."""
    all_facts = [f for d in docs for f in extract_facts(d, limit=40)]
    out: list[tuple[Fact, Fact]] = []

    def toks(f: Fact) -> set[str]:
        return {w.lower() for w in f.subject.split()}

    for i, a in enumerate(all_facts):
        for b in all_facts[i + 1 :]:
            if a.source.split(" ")[0] == b.source.split(" ")[0]:
                continue
            ta, tb = toks(a), toks(b)
            if ta and tb and len(ta & tb) / len(ta | tb) >= 0.6 and set(a.values) != set(b.values):
                out.append((a, b))
                if len(out) >= limit:
                    return out
    return out
