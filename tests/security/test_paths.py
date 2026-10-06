"""Names and paths that come from outside (a test id in a dataset, an attachment reference in a test, an artifact id in a
request, the name a web page suggests for a download) must never choose where AgentLab reads or writes (spec section 10).

The design keeps most of this structural, and these tests pin that design down: artifacts are stored under the hash of their
content, attachments are read from one directory, and a human-readable name is only ever metadata."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from agentlab.core.errors import InfrastructureError, PolicyBlocked, UserError
from agentlab.core.models import TargetSpec
from agentlab.documents.attachments import MAX_ATTACHMENT_BYTES, load_attachment
from agentlab.documents.generated import MAX_GENERATED_BYTES, MAX_PICTURE_SIDE
from agentlab.evaluation.context import PlaceholderResolver
from agentlab.fixtures import ChatbotAgent
from agentlab.orchestrator import RunOptions
from agentlab.storage.artifacts import LocalArtifactStore
from tests.support.lab import Lab


def everything_under(root: Path) -> set[Path]:
    return {p.resolve() for p in root.rglob("*")}


# ============================================================================================== artifact storage
@pytest.mark.parametrize(
    "artifact_id",
    [
        "../../etc/passwd",
        "sha256-../../etc/passwd",
        "sha256-" + "0" * 63,
        "sha256-" + "g" * 64,
        "sha256-" + "a" * 64 + "/../../x",
        "/etc/passwd",
        "sha256-" + "a" * 64 + "\x00.json",
        "",
        "sha256-",
    ],
)
def test_an_artifact_id_that_is_not_a_hash_never_reaches_the_filesystem(tmp_path: Path, artifact_id: str) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    for operation in (store.get, store.ref, store.local_path):
        with pytest.raises(InfrastructureError, match="invalid artifact id"):
            operation(artifact_id)


def test_the_name_given_to_an_artifact_is_a_label_never_a_location(tmp_path: Path) -> None:
    root = tmp_path / "store"
    store = LocalArtifactStore(root)
    before = everything_under(tmp_path)
    for name in ("../../escape.txt", "/etc/cron.d/evil", "..\\..\\escape.txt", "a/b/../../../c", "x\x00y", "evil\n.sh"):
        ref = store.put(b"payload " + name.encode("utf-8", "replace"), kind="note", name=name, media_type="text/plain")
        stored = store.local_path(ref.id)
        assert stored.is_relative_to(root.resolve()) and stored.name == ref.sha256, "the file is named by its content"
        assert stored.parent.name == ref.sha256[:2]
        assert store.ref(ref.id).name == name, "the label is kept as the label it is"
    created = everything_under(tmp_path) - before
    assert all(p.is_relative_to(root.resolve()) for p in created), sorted(
        str(p) for p in created if not p.is_relative_to(root.resolve())
    )
    assert not (tmp_path / "escape.txt").exists() and not Path("/etc/cron.d/evil").exists()


def test_identical_content_is_stored_once_and_sensitive_content_lives_in_a_private_area(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    a = store.put(b"same", kind="note", name="one", media_type="text/plain")
    b = store.put(b"same", kind="note", name="two", media_type="text/plain")
    assert a.id == b.id and a.sha256 == hashlib.sha256(b"same").hexdigest()
    secret = store.put(b"trace", kind="trace", sensitivity="restricted", media_type="application/json")
    path = store.local_path(secret.id)
    assert "restricted" in path.parts and path.stat().st_mode & 0o077 == 0, "owner only"
    assert path.parent.stat().st_mode & 0o077 == 0


# ============================================================================================ attachment references
def fixtures_dir(tmp_path: Path) -> Path:
    base = tmp_path / "fixtures"
    base.mkdir()
    (base / "ok.txt").write_text("fine")
    (tmp_path / "outside.txt").write_text("outside the fixtures directory")
    (base / "sub").mkdir()
    (base / "sub" / "deep.txt").write_text("deep")
    return base


def test_an_attachment_is_read_from_the_fixtures_directory_and_nowhere_else(tmp_path: Path) -> None:
    base = fixtures_dir(tmp_path)
    assert base64.b64decode(load_attachment("ok.txt", base).content_b64) == b"fine"
    assert base64.b64decode(load_attachment("sub/deep.txt", base).content_b64) == b"deep"
    for ref in (
        "../outside.txt",
        "sub/../../outside.txt",
        str(tmp_path / "outside.txt"),
        "/etc/passwd",
        "sub/../../../etc/passwd",
    ):
        with pytest.raises(PolicyBlocked, match="escapes the fixtures directory"):
            load_attachment(ref, base)


def test_a_link_inside_the_fixtures_directory_cannot_lead_out_of_it(tmp_path: Path) -> None:
    base = fixtures_dir(tmp_path)
    (base / "innocent.txt").symlink_to(tmp_path / "outside.txt")
    (base / "innocent-dir").symlink_to(tmp_path, target_is_directory=True)
    for ref in ("innocent.txt", "innocent-dir/outside.txt"):
        with pytest.raises(PolicyBlocked, match="escapes the fixtures directory"):
            load_attachment(ref, base)


def test_a_missing_or_oversized_attachment_is_a_setup_problem_with_a_clear_message(tmp_path: Path) -> None:
    base = fixtures_dir(tmp_path)
    with pytest.raises(UserError, match="not found"):
        load_attachment("nope.txt", base)
    (base / "big.bin").write_bytes(b"\0" * (MAX_ATTACHMENT_BYTES + 1))
    with pytest.raises(UserError, match="larger than"):
        load_attachment("big.bin", base)


@pytest.mark.parametrize("how", ["declared secret", "attempt variable"])
def test_a_value_substituted_into_a_reference_cannot_be_used_to_climb_out_either(tmp_path: Path, how: str) -> None:
    """Placeholders are expanded first and the path is checked afterwards, so a value that arrives through one (a secret
    declared in target.yaml, the address of the test site) is held to the same rule as text typed into the reference."""
    base = fixtures_dir(tmp_path)
    resolver = PlaceholderResolver()
    if how == "declared secret":
        resolver.register_known(["../outside.txt"])
        reference = "{{canary:declared_1}}"
    else:
        resolver = resolver.with_variables(site_url="../outside.txt")
        reference = "{{site_url}}"
    with pytest.raises(PolicyBlocked, match="escapes the fixtures directory"):
        load_attachment(reference, base, resolver)


def test_a_marker_placed_in_a_text_attachment_is_this_runs_marker_and_nothing_else_is_read(tmp_path: Path) -> None:
    base = fixtures_dir(tmp_path)
    (base / "note.txt").write_text("remember {{canary:memo}} please", encoding="utf-8")
    resolver = PlaceholderResolver()
    text = base64.b64decode(load_attachment("note.txt", base, resolver).content_b64).decode()
    assert resolver.canary("memo") in text and "{{" not in text


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("gen://png/solid?color=red&size=30000", "outside the range"),
        ("gen://png/solid?color=red&size=0", "outside the range"),
        ("gen://png/solid?color=red&size=-5", "outside the range"),
        ("gen://png/text?text=" + "A" * 50_000, "too large"),
        ("gen://txt/text?text=" + "A" * (MAX_GENERATED_BYTES + 1), "larger than"),
        ("gen://nothing/at-all", "unknown gen:// recipe"),
        ("gen://png/solid?color=chartreuse", "unknown colour"),
        ("gen://bin/random?size=lots", "invalid literal"),
    ],
)
def test_a_generated_attachment_that_would_exhaust_memory_is_refused_before_it_is_built(
    tmp_path: Path, reference: str, message: str
) -> None:
    """A dataset may come from somebody else. A recipe cannot ask for a picture of 30000 x 30000 pixels (that was a
    ``MemoryError`` before the limit existed) and every refusal is a setup problem with a message, not a crash."""
    with pytest.raises(UserError, match=message):
        load_attachment(reference, tmp_path)


def test_generated_attachments_are_bounded_so_a_test_cannot_exhaust_the_disk(tmp_path: Path) -> None:
    huge = load_attachment("gen://bin/random?size=999999999", tmp_path)
    assert len(base64.b64decode(huge.content_b64)) <= MAX_GENERATED_BYTES
    small = load_attachment("gen://bin/random?size=64&seed=3", tmp_path)
    assert len(base64.b64decode(small.content_b64)) == 64
    biggest_picture = load_attachment(f"gen://png/solid?color=blue&size={MAX_PICTURE_SIDE}", tmp_path)
    assert base64.b64decode(biggest_picture.content_b64).startswith(b"\x89PNG")


# ================================================================================ identifiers from a user's dataset
async def test_a_hostile_test_name_or_id_in_a_dataset_cannot_move_a_single_file(tmp_path: Path) -> None:
    dataset = tmp_path / "scenarios.yaml"
    dataset.write_text(
        "- name: '../../../../tmp/agentlab-name-escape'\n  input: Hello\n  must_not_be_empty: true\n"
        "- id: '../../../../tmp/agentlab-id-escape'\n  name: Explicit id\n  input: What is the capital of Spain?\n"
        "  must_contain: [Madrid]\n",
        encoding="utf-8",
    )
    root = tmp_path / "lab"
    async with Lab(root, formats=["json", "html", "md"]) as lab:
        with ChatbotAgent.build("correct").deployed() as target:
            out = await lab.run(
                TargetSpec(**target),
                RunOptions(intensity="quick", suite="functional", second_wave=False, user_test_files=[dataset]),
            )
        user = [r for r in out.results if r.test_id.startswith("USER-") or "escape" in r.test_id]
        assert user, "the dataset was loaded"
        written = lab.files()
    assert all(p.resolve().is_relative_to(root.resolve()) for p in written)
    assert not list(Path("/tmp").glob("agentlab-*-escape*")) and not (tmp_path / "tmp").exists()
    assert {p.suffix for p in written} >= {".db", ".json", ".html", ".md"}
