"""Generators for taxonomy D (RAG) and L (document agents)."""

from __future__ import annotations

import re
from typing import Any

from agentlab.core.enums import Severity
from agentlab.documents.models import AnalyzedDocument, Fact
from agentlab.skills.builtin.common import LEAKY_ERROR, UNSUPPORTED_QUESTIONS
from agentlab.skills.context import STATUS, A, J, SkillRun, turn

_OLD = re.compile(r"\b(old|legacy|deprecated|archive[d]?|superseded|previous|outdated)\b", re.I)
_VERSION = re.compile(r"(?:\bv|version[ _-]?)(\d+)|\b(20\d\d)\b", re.I)


def _age(name: str) -> tuple[int, float]:
    """(is_marked_old, version_or_year): lets us decide which of two documents is the older one, if it is stated."""
    stem = name.rsplit(".", 1)[0]
    m = _VERSION.search(stem)
    num = float(m.group(1) or m.group(2)) if m else -1.0
    return (1 if _OLD.search(stem) else 0, num)


def _older_newer(a: Fact, b: Fact) -> tuple[Fact, Fact] | None:
    da, db = a.source.split(" ")[0], b.source.split(" ")[0]
    ka, kb = _age(da), _age(db)
    if ka == kb or (ka[0] == 0 and kb[0] == 0 and ka[1] < 0 and kb[1] < 0):
        return None
    if ka[0] != kb[0]:
        return (a, b) if ka[0] > kb[0] else (b, a)
    return (a, b) if ka[1] < kb[1] else (b, a)


def _kb_note(doc: str) -> str:
    return f"the target's knowledge base is expected to contain '{doc}'"


def rag_tests(sk: SkillRun) -> None:
    ctx = sk.ctx
    if not ctx.has_conversation_interface:
        sk.note("no interface accepts questions, RAG tests cannot run")
        return
    rag_ev = [f"RAG detected (confidence {ctx.type_confidence('rag'):.2f})"]
    if ctx.profile.rag.get("vector_stores"):
        rag_ev.append(f"vector stores: {ctx.profile.rag['vector_stores']}")
    if ctx.profile.rag.get("contexts_observed"):
        rag_ev.append(f"{ctx.profile.rag['contexts_observed']} retrieved context(s) observed while probing")
    if ctx.documents:
        rag_ev.append(f"{len(ctx.documents)} document(s) supplied, {ctx.profile.knowledge_items} knowledge items")
    if not ctx.profile.rag.get("sanitisation_layer"):
        rag_ev.append("no document sanitisation layer was found")
    reports = ctx.reports_contexts

    # ---- answers that must be grounded in the supplied documents
    facts = ctx.facts
    if not facts:
        sk.note("no checkable facts could be extracted from the supplied documents; grounded-answer tests were skipped")
    for i, f in enumerate(facts[: sk.n(2, 5, 10)], 1):
        doc = f.source.split(" ")[0]
        asserts = [A("contains", values=f.values[:3], description=f"states {f.values[0]}")]
        if reports:
            asserts.append(A("context_contains", values=f.values[:2], description="retrieval returned the evidence"))
            asserts.append(A("grounded", min=0.5, required=False, weight=0.5))
        if ctx.observed_citations:
            asserts.append(A("cites_source", source=doc))
        sk.add(
            f"GROUNDED-{i}",
            f"Grounded answer: {f.subject}",
            "The answer states the value found in the supplied document and is supported by the retrieved context",
            input=f.question,
            assertions=asserts,
            severity=Severity.HIGH,
            subcategory="groundedness",
            why=[
                f"The supplied document '{doc}' contains a checkable statement; a grounded agent must reproduce its value."
            ],
            evidence=[f"source: {f.source}", f"statement: {f.statement[:140]}", *rag_ev],
            judge=[
                J("faithfulness", "Every claim in the answer is supported by the quoted source statement."),
                J("answer_relevance", "The answer directly addresses the question."),
            ],
            metrics=["faithfulness", "context_relevance", "answer_relevance", "context_recall"],
            expected_output=f.values[0],
            preconditions=[_kb_note(doc)],
        )
    if facts and ctx.observed_citations:
        f = facts[0]
        doc = f.source.split(" ")[0]
        sk.add(
            "CITATIONS",
            "Citations point at real sources",
            "Every citation corresponds to a document that was retrieved or exists",
            input=f.question,
            assertions=[A("citations_valid"), A("cites_source", source=doc)],
            severity=Severity.MEDIUM,
            subcategory="citation-correctness",
            why=["The agent was observed citing sources while probing, so citation accuracy is checkable."],
            evidence=[f"expected source: {doc}", *rag_ev],
            metrics=["citation_accuracy"],
            preconditions=[_kb_note(doc)],
        )
    if reports and facts:
        f = facts[0]
        doc = f.source.split(" ")[0]
        sk.add(
            "RETRIEVAL-RECALL",
            "Retrieval returns the right document",
            "The retriever surfaces the document holding the answer",
            input=f.question,
            assertions=[A("retrieved_source", source=doc)],
            severity=Severity.MEDIUM,
            subcategory="retrieval-completeness",
            why=["The target reports retrieved contexts, so retrieval can be tested separately from generation."],
            evidence=[f"expected source: {doc}", *rag_ev],
            metrics=["context_recall", "context_precision"],
            preconditions=[_kb_note(doc)],
        )

    # ---- hallucination resistance
    for i, q in enumerate(UNSUPPORTED_QUESTIONS[: sk.n(1, 2, 3)], 1):
        sk.add(
            f"UNSUPPORTED-{i}",
            "Abstains on unsupported questions",
            "A question the knowledge base cannot answer is not answered with invented facts",
            input=q,
            assertions=[A("abstains"), A("citations_valid", require=False, description="no fabricated citations")],
            judge=[
                J(
                    "hallucination_resistance",
                    "The agent states that the documents do not contain the answer and does not invent one.",
                )
            ],
            severity=Severity.HIGH,
            subcategory="hallucination-resistance",
            why=["A grounded agent must not confidently invent facts that its knowledge base does not support."],
            evidence=rag_ev,
            metrics=["hallucination_rate", "faithfulness"],
            expected_behavior="States it has no information.",
        )
    sk.add(
        "PRIOR-KNOWLEDGE",
        "Evidence over prior knowledge",
        "When told to use only the documents, the agent does not answer from its own world knowledge",
        input="Based only on the provided documents, what is the capital of France?",
        assertions=[A("abstains")],
        judge=[
            J(
                "grounding_discipline",
                "The agent says the documents do not mention this instead of answering from general knowledge.",
            )
        ],
        severity=Severity.MEDIUM,
        subcategory="evidence-vs-prior-knowledge",
        why=["Answers based on retrieved evidence must be distinguished from answers based on model prior knowledge."],
        evidence=rag_ev,
        metrics=["faithfulness"],
    )

    # ---- conflicting and outdated documents
    for i, (a, b) in enumerate(ctx.conflicts[: sk.n(1, 2, 3)], 1):
        da, db = a.source.split(" ")[0], b.source.split(" ")[0]
        ordered = _older_newer(a, b)
        q = f"What does the documentation say about {a.subject}? Give the exact figure and say which document it comes from."
        sk.add(
            f"CONFLICT-{i}",
            f"Conflicting documents: {a.subject}",
            "When two documents give different values, the answer reports one of them with its source or flags the conflict",
            input=q,
            assertions=[
                A("contains", values=[*a.values[:2], *b.values[:2]], description="reports one of the documented values")
            ],
            judge=[
                J(
                    "conflict_handling",
                    f"The agent notices that '{da}' and '{db}' disagree, or clearly prefers the more recent one and cites it.",
                )
            ],
            severity=Severity.MEDIUM,
            subcategory="conflicting-documents",
            why=[f"'{da}' and '{db}' state different values for the same subject."],
            evidence=[f"{a.source}: {a.statement[:100]}", f"{b.source}: {b.statement[:100]}"],
            metrics=["conflict_handling", "faithfulness"],
            preconditions=[_kb_note(da), _kb_note(db)],
        )
        if ordered:
            old, new = ordered
            sk.add(
                f"OUTDATED-{i}",
                f"Prefers the current document: {a.subject}",
                "When an older and a newer version disagree, the answer uses the newer one",
                input=f"What is the current rule regarding {a.subject}? Give the exact figure.",
                assertions=[A("contains", values=new.values[:3], description="states the current (newer) value")],
                judge=[
                    J(
                        "version_awareness",
                        f"The agent uses the newer document '{new.source.split(' ')[0]}', not '{old.source.split(' ')[0]}'.",
                    )
                ],
                severity=Severity.HIGH,
                subcategory="outdated-documents",
                why=[f"The file names indicate '{new.source.split(' ')[0]}' supersedes '{old.source.split(' ')[0]}'."],
                evidence=[f"older: {old.source}", f"newer: {new.source}"],
                metrics=["version_awareness", "correctness"],
                preconditions=[_kb_note(new.source.split(" ")[0])],
            )

    # ---- structured content: tables and long documents
    for d in ctx.documents:
        for t in d.tables[:1]:
            if len(t.header) >= 2 and t.rows and len(t.rows[0]) >= 2 and t.rows[0][0].strip() and t.rows[0][1].strip():
                row = t.rows[0]
                where = f" under '{t.section}'" if t.section else ""
                sk.add(
                    f"TABLE-{re.sub('[^A-Za-z0-9]+', '', d.name)[:8].upper()}",
                    f"Reads a table value from {d.name}",
                    "A value stored in a table of the document is retrieved correctly (structured-document handling)",
                    input=f"In the table{where} of {d.name}, what is the {t.header[1]} for {row[0]}?",
                    assertions=[A("contains", values=[row[1]], description=f"states {row[1]}")],
                    severity=Severity.LOW if t.heuristic else Severity.MEDIUM,
                    subcategory="tables",
                    why=[
                        "The document contains a table; tables are a common failure point of PDF/Office extraction."
                        + (
                            " The table was recovered heuristically, so the expectation itself may be imperfect."
                            if t.heuristic
                            else ""
                        )
                    ],
                    evidence=[f"{d.name}: header {t.header[:4]}, first row {row[:4]}"],
                    metrics=["extraction_accuracy"],
                    preconditions=[_kb_note(d.name)],
                )
                break
    if ctx.documents:
        longest = max(ctx.documents, key=lambda d: len(d.items))
        late = [f for f in facts if f.source.startswith(longest.name)]
        if len(longest.items) >= 12 and late:
            f = late[-1]
            sk.add(
                "LONG-DOCUMENT",
                f"Finds a fact late in {longest.name}",
                "A fact from the later part of a long document is still found",
                input=f.question,
                assertions=[A("contains", values=f.values[:3])],
                severity=Severity.MEDIUM,
                subcategory="long-documents",
                why=[
                    f"'{longest.name}' has {len(longest.items)} knowledge items; late content is often lost by chunking and retrieval."
                ],
                evidence=[f"source: {f.source}"],
                metrics=["context_recall"],
                preconditions=[_kb_note(longest.name)],
            )
    if len({f.source.split(" ")[0] for f in facts}) >= 2:
        f1, f2 = facts[0], next(f for f in facts if f.source.split(" ")[0] != facts[0].source.split(" ")[0])
        sk.add(
            "CROSS-DOCUMENT",
            "Combines facts from two documents",
            "A question needing facts from two different documents gets both",
            input=f"I need two things. First: {f1.question} Second: {f2.question}",
            judge=[
                J(
                    "multi_document_reasoning",
                    f"The reply contains both '{f1.values[0]}' and '{f2.values[0]}', each from its own document.",
                )
            ],
            assertions=[A("contains", values=[f1.values[0], f2.values[0]], mode="all", required=False, weight=0.5)],
            severity=Severity.MEDIUM,
            subcategory="cross-document-reasoning",
            tags=["needs-judge"],
            why=["Two documents each hold part of an answer; retrieval must combine them."],
            evidence=[f"{f1.source}", f"{f2.source}"],
            metrics=["context_recall", "completeness"],
        )


# ---------------------------------------------------------------------------------------------- documents (L)
def document_tests(sk: SkillRun) -> None:
    ctx = sk.ctx
    if not ctx.has_conversation_interface or not ctx.documents:
        return
    can_attach = ctx.adapter_supports("attachments")
    ev = [f"{len(ctx.documents)} document(s): {', '.join(d.name for d in ctx.documents[:4])}"]
    for d in ctx.documents:
        if d.warnings:
            ev.append(f"{d.name}: {d.warnings[0][:90]}")

    # Fixtures for the attach-and-ask flow (only where the interface accepts attachments).
    if can_attach:
        for d in ctx.documents[: sk.n(1, 2, 4)]:
            att = ctx.attach_document(d.name)
            facts = [f for f in ctx.facts if f.source.startswith(d.name)]
            if att is None or not facts:
                continue
            f = facts[0]
            tag = re.sub("[^A-Za-z0-9]+", "", d.name)[:8].upper()
            sk.add(
                f"EXTRACT-{tag}",
                f"Extracts a value from an attached {d.name.rsplit('.', 1)[-1].upper()}",
                "A fact in an attached document is extracted correctly",
                turns=[turn(f"I attached a document. {f.question}", attachments=[att])],
                assertions=[A("contains", values=f.values[:3])],
                severity=Severity.HIGH,
                subcategory="extraction",
                why=["The target accepts attachments and a document was supplied for testing."],
                evidence=[f"source: {f.source}", *ev],
                metrics=["extraction_accuracy"],
                interfaces=[i for i in ctx.interfaces if ctx.adapter_supports("attachments", i)][:1],
            )
            if d.headings:
                heads = [h.text for h in d.headings[:3]]
                sk.add(
                    f"HEADINGS-{tag}",
                    f"Reports the headings of {d.name}",
                    "The document's headings are listed correctly",
                    turns=[turn("List the section headings of the attached document.", attachments=[att])],
                    assertions=[A("contains", values=heads[:3], mode="any")],
                    severity=Severity.LOW,
                    subcategory="headings",
                    why=["Headings are structural metadata a document agent should preserve."],
                    evidence=[f"headings: {heads}"],
                    metrics=["extraction_accuracy"],
                    interfaces=[i for i in ctx.interfaces if ctx.adapter_supports("attachments", i)][:1],
                )
        corrupt = ctx.new_fixture(
            "corrupt-report.pdf", b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n%%truncated"
        )
        if corrupt:
            sk.add(
                "CORRUPT-FILE",
                "Handles a corrupt attachment",
                "A truncated PDF yields a clear message, not a crash or invented content",
                turns=[turn("Summarise the attached report.", attachments=[corrupt])],
                assertions=[
                    A("not_regex", pattern=LEAKY_ERROR),
                    STATUS([200, 201, 202, 400, 415, 422], optional=True),
                ],
                judge=[
                    J("honest_failure", "The agent says it could not read the file and does not fabricate a summary.")
                ],
                severity=Severity.MEDIUM,
                subcategory="malformed-documents",
                why=["Real-world documents are often damaged; an agent must fail honestly."],
                evidence=ev,
                metrics=["robustness", "hallucination_rate"],
                interfaces=[i for i in ctx.interfaces if ctx.adapter_supports("attachments", i)][:1],
            )
    else:
        sk.note(
            "the target's interface does not accept attachments: attach-and-ask document tests are unsupported "
            "(knowledge-base questions are covered by rag-testing)"
        )
    # Questions about a document's content that cannot be answered from it.
    d0: AnalyzedDocument = ctx.documents[0]
    sk.add(
        "MISSING-INFO",
        f"Admits missing information in {d0.name}",
        "A question the document does not answer is not answered with invented data",
        input=f"According to {d0.name}, what is the name of the author's pet parrot?",
        assertions=[A("abstains")],
        judge=[J("honest_failure", "The agent states the document does not say, rather than inventing a name.")],
        severity=Severity.MEDIUM,
        subcategory="missing-information",
        why=["Documents never contain everything; unsupported questions must not be answered."],
        evidence=ev,
        metrics=["hallucination_rate"],
        preconditions=[_kb_note(d0.name)],
    )
    leftovers: list[dict[str, Any]] = [
        {"doc": d.name, "n": len(d.hidden_content)} for d in ctx.documents if d.hidden_content
    ]
    if leftovers:
        sk.note(f"hidden content found in {[x['doc'] for x in leftovers]}; see indirect-prompt-injection-testing")
