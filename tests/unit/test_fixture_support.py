"""The pieces the fixture agents are built from: synthetic attachments, the image reader and the lexical knowledge base.

They are small and deterministic by design; these tests pin that down so that a planted defect is never found or missed
because a helper changed underneath it.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from agentlab.documents.generated import COLOURS, FONT, generate, is_generated
from agentlab.fixtures import REGISTRY, fixture_class, make_app, vision
from agentlab.fixtures.base import ChatRequest
from agentlab.fixtures.kb import KnowledgeBase, UnreadableDocument, chunks_from_bytes, tokens


# ------------------------------------------------------------------------------------------------- gen:// recipes
def test_generated_attachments_are_deterministic() -> None:
    for ref in (
        "gen://png/solid?color=red",
        "gen://png/text?text=CODE%204821",
        "gen://corrupt/png",
        "gen://corrupt/pdf",
        "gen://bin/random?size=64&seed=3",
        "gen://pdf/text?text=Hello",
        "gen://docx/text?text=Hello",
        "gen://txt/text?text=Hello",
    ):
        first, second = generate(ref), generate(ref)
        assert first.data == second.data and first.data, ref
        assert is_generated(ref)


def test_unknown_recipes_and_other_schemes_are_refused() -> None:
    with pytest.raises(ValueError, match="unknown gen:// recipe"):
        generate("gen://png/unicorn")
    with pytest.raises(ValueError, match="not a gen:// reference"):
        generate("file:///etc/passwd")
    assert not is_generated("notes/meeting.txt")


def test_random_bytes_differ_by_seed_and_are_capped() -> None:
    assert generate("gen://bin/random?seed=1").data != generate("gen://bin/random?seed=2").data
    assert len(generate("gen://bin/random?size=999999999").data) <= 2_000_000


def test_every_glyph_is_five_by_seven() -> None:
    for char, rows in FONT.items():
        assert len(rows) == 7 and all(len(r) == 5 for r in rows), repr(char)


# ------------------------------------------------------------------------------------------------ the image reader
@pytest.mark.parametrize("colour", sorted(COLOURS))
def test_the_reader_names_the_colour_of_a_solid_picture(colour: str) -> None:
    image = vision.decode_png(generate(f"gen://png/solid?color={colour}").data)
    assert vision.dominant_colour(image) == colour
    assert vision.read_text(image) is None  # a plain picture holds no text


@pytest.mark.parametrize("text", ["CODE 4821", "ORDER 7305", "HELLO WORLD", "A-1: 2/3 (OK)"])
def test_text_pictures_round_trip_through_the_reader(text: str) -> None:
    image = vision.decode_png(generate("gen://png/text?text=" + text.replace(" ", "%20")).data)
    assert vision.read_text(image) == text
    assert vision.dominant_colour(image) == "white"  # the background, not the glyphs


def test_a_truncated_png_is_reported_not_guessed() -> None:
    with pytest.raises(vision.ImageError):
        vision.decode_png(generate("gen://corrupt/png").data)
    with pytest.raises(vision.ImageError, match="not a PNG"):
        vision.decode_png(b"GIF89a....")


# --------------------------------------------------------------------------------------------- the knowledge base
def test_retrieval_finds_the_document_that_holds_the_answer() -> None:
    kb = KnowledgeBase.default()
    top, _ = kb.search(tokens("How many days of paid annual leave do full-time employees get?"), k=1)[0]
    assert top.source == "hr-policy.pdf"


def test_restricted_documents_are_withheld_unless_asked_for() -> None:
    kb = KnowledgeBase.default()
    query = tokens("What is the CEO's annual base salary?")
    assert all(c.source != "executive-compensation.md" for c, _ in kb.search(query, k=5))
    assert any(c.source == "executive-compensation.md" for c, _ in kb.search(query, k=5, include_restricted=True))


def test_a_superseded_document_ranks_below_the_current_one() -> None:
    kb = KnowledgeBase.default()
    hits = kb.search(tokens("paid annual leave per calendar year"), k=5)
    sources = [c.source for c, _ in hits]
    assert sources.index("hr-policy.pdf") < sources.index("hr-policy-v1.md")


def test_attached_files_become_knowledge() -> None:
    chunks = chunks_from_bytes("note.txt", b"The warehouse moves to Leeds in March.")
    kb = KnowledgeBase([]).with_chunks(chunks)
    assert kb.search(tokens("Where does the warehouse move?"), k=1)[0][0].source == "note.txt"


def test_unreadable_files_are_refused() -> None:
    with pytest.raises(UnreadableDocument):
        chunks_from_bytes("broken.pdf", generate("gen://corrupt/pdf").data)
    with pytest.raises(UnreadableDocument):
        chunks_from_bytes("random.bin", generate("gen://bin/random").data)


# ------------------------------------------------------------------------------------------------- the HTTP service
def test_the_fixture_service_guards_its_endpoint() -> None:
    agent = fixture_class("chatbot").build("correct")
    client = TestClient(make_app(agent, token="s3cret"))
    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/chat", json={"message": "hi"}).status_code == 401
    ok = client.post(
        "/chat", json={"message": "What is the capital of Spain?"}, headers={"Authorization": "Bearer s3cret"}
    )
    assert ok.status_code == 200 and "Madrid" in ok.json()["reply"]
    big = client.post("/chat", json={"message": "x" * 200_000}, headers={"Authorization": "Bearer s3cret"})
    assert big.status_code == 413


def test_a_defect_changes_behaviour_and_the_correct_build_does_not() -> None:
    import asyncio

    async def ask(variant: str) -> str:
        agent = fixture_class("chatbot").build(variant)
        reply = await agent.reply(ChatRequest(message="What is 38 + 47?"), agent.session("s"))
        return reply.text

    assert asyncio.run(ask("correct")).endswith("85")
    assert asyncio.run(ask("wrong_arithmetic")).endswith("87")


def test_every_fixture_documents_its_defects_and_builds_every_variant() -> None:
    for kind, cls in REGISTRY.items():
        assert cls.DEFECTS, kind
        assert cls.build("correct").defects == set(), kind
        assert cls.build("flawed").defects == set(cls.DEFECTS), kind
        for name, description in cls.DEFECTS.items():
            assert description and not description.startswith(name), f"{kind}.{name}: describe the behaviour"
        with pytest.raises(ValueError, match="unknown"):
            cls.build("not_a_defect")


def test_base64_attachments_survive_the_wire_format() -> None:
    attachment = generate("gen://txt/text?text=Hello")
    encoded = base64.b64encode(attachment.data).decode()
    assert base64.b64decode(encoded) == attachment.data
