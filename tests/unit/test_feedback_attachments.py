"""Attachments without a server: what is kept, who may see it, the limits, the scanner, and removal.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import inspect, text

from agentlab.core.config import AttachmentsConfig, FeedbackConfig
from agentlab.core.errors import NotFoundError, PolicyBlocked, UserError
from agentlab.feedback import QuotaExceeded
from agentlab.feedback.attachments import AttachmentService
from agentlab.storage.artifacts import LocalArtifactStore
from tests.unit.test_feedback_service import ISSUE, Clock, World

OPENAI = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
LOG = (
    f"2026-10-12 10:00:01 INFO start\nAuthorization: Bearer {OPENAI}\n"
    "2026-10-12 10:00:02 ERROR contact jane@example.com failed: TimeoutError\n"
).encode()


class AttachWorld(World):
    """A submission world with attachments kept in a real (temporary) restricted store."""

    def __init__(self, tmp_path: Path, clock: Clock, **feedback: Any) -> None:
        super().__init__(tmp_path, clock, **feedback)
        self.root = tmp_path / "artifacts"
        self.store = LocalArtifactStore(self.root)
        self.attach = AttachmentService(self.svc, self.store, self.config, clock=clock)

    def add(self, who: Any, ident: str, name: str, data: bytes, **kw: Any):  # type: ignore[no-untyped-def]
        return self.attach.add(who, ident, filename=name, declared_type=kw.pop("declared", ""), data=data, **kw)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make(tmp_path: Path, clock: Clock):  # type: ignore[no-untyped-def]
    return lambda **feedback: AttachWorld(tmp_path, clock, **feedback)


@pytest.fixture
def world(make):  # type: ignore[no-untyped-def]
    return make()


def stored_files(root: Path) -> list[Path]:
    return [p for area in ("objects", "restricted") for p in (root / area).rglob("*") if p.is_file()]


# =============================================================================================== what is kept
def test_a_text_file_is_kept_as_uploaded_beside_a_sanitized_copy(world: AttachWorld) -> None:
    alice = world.person("alice")
    made = world.file(alice, ISSUE)
    att = world.add(alice, made.id, "run.log", LOG, declared="text/plain")
    assert (att.name, att.extension, att.type, att.text_like) == ("run.log", "log", "text/plain", True)
    assert att.secrets.status == "redacted" and att.secrets.counts["credential_header"] == 1
    assert att.secrets.counts["email"] == 1 and att.sanitized_available and att.mine
    assert att.scan.status == "not_scanned" and "no malware scanner" in att.scan.detail

    original, _ = world.attach.download(alice, made.id, att.id)
    sanitized, _ = world.attach.download(alice, made.id, att.id, sanitized=True)
    assert original == LOG, "the original is never changed"
    assert OPENAI.encode() not in sanitized and b"jane@example.com" not in sanitized and b"TimeoutError" in sanitized
    preview = world.attach.preview(alice, made.id, att.id)
    assert preview.kind == "text" and OPENAI not in (preview.text or "") and preview.sanitized is True


def test_a_clean_text_file_has_no_second_copy_and_an_image_is_not_inspected_for_secrets(world: AttachWorld) -> None:
    alice = world.person("alice")
    made = world.file(alice, ISSUE)
    clean = world.add(alice, made.id, "notes.txt", b"The page shows a blank panel.\n")
    assert clean.secrets.status == "none" and clean.sanitized_available is False
    image = world.add(alice, made.id, "screenshot.png", PNG, declared="image/jpeg")
    assert image.type == "image/png", "what the content says, not what the browser said"
    assert image.declared_type == "image/jpeg" and image.secrets.status == "not_applicable"
    assert world.attach.preview(alice, made.id, image.id).kind == "image"
    with pytest.raises(NotFoundError, match="no sanitized copy"):
        world.attach.download(alice, made.id, image.id, sanitized=True)


def test_the_bytes_are_in_the_restricted_area_under_a_name_made_from_their_hash(world: AttachWorld) -> None:
    alice = world.person("alice")
    made = world.file(alice, ISSUE)
    world.add(alice, made.id, "../../etc/cron.d/evil.log", LOG)
    files = stored_files(world.root)
    relative = [p.relative_to(world.root) for p in files]
    assert files and all(r.parts[0] == "restricted" for r in relative), "nothing is in the area that is not restricted"
    for path in files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, "only the server's user may read an attachment"
        assert len(path.name) == 64 and all(c in "0123456789abcdef" for c in path.name)
    assert not any("evil" in str(r) or "cron" in str(r) for r in relative), "a file name is a label, never a path"
    assert (world.root / "restricted").stat().st_mode & 0o777 == 0o700


def test_a_zip_is_kept_and_summarised_but_not_opened_for_secrets(world: AttachWorld) -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("logs/app.log", LOG)
        z.writestr("env.json", b"{}")
    alice = world.person("alice")
    made = world.file(alice, ISSUE)
    att = world.add(alice, made.id, "diagnostics.zip", buf.getvalue())
    assert att.archive and att.archive["entries"] == 2 and att.archive["names"] == ["app.log", "env.json"]
    assert att.secrets.status == "not_applicable" and att.text_like is False and att.sanitized_available is False


def test_a_text_file_over_the_inspection_limit_is_kept_and_says_it_was_not_inspected(make) -> None:  # type: ignore[no-untyped-def]
    w = make(attachments=AttachmentsConfig(sanitize_max_mb=1))
    alice = w.person("alice")
    made = w.file(alice, ISSUE)
    big = (b"INFO line with nothing in it\n" * 50_000)[:1_200_000]
    att = w.add(alice, made.id, "big.log", big)
    assert att.secrets.status == "not_inspected" and att.sanitized_available is False
    assert w.attach.preview(alice, made.id, att.id).message.startswith("This file was too large")


# ====================================================================================== who may see what
def test_another_reporter_cannot_list_read_download_or_remove_an_attachment(world: AttachWorld) -> None:
    alice, bob, maria = world.person("alice"), world.person("bob"), world.maintainer()
    made = world.file(alice, ISSUE)
    att = world.add(alice, made.id, "run.log", LOG)
    for call in (
        lambda: world.attach.of_submission(bob, made.id),
        lambda: world.attach.get(bob, made.id, att.id),
        lambda: world.attach.download(bob, made.id, att.id),
        lambda: world.attach.preview(bob, made.id, att.id),
        lambda: world.attach.remove(bob, made.id, att.id),
        lambda: world.add(bob, made.id, "x.txt", b"hello"),
    ):
        with pytest.raises(NotFoundError, match="no such submission"):
            call()
    other = world.file(bob, ISSUE, title="Another problem entirely")
    with pytest.raises(NotFoundError, match="no such attachment"):
        world.attach.get(bob, other.id, att.id)  # an attachment id does not work on a different submission
    assert [a.id for a in world.attach.of_submission(maria, made.id)] == [att.id]


def test_a_private_submissions_attachments_follow_the_submissions_rule(world: AttachWorld) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    private = world.file(alice, ISSUE, suspected_security=True)
    att = world.add(alice, private.id, "dump.log", LOG)
    assert world.attach.get(maria, private.id, att.id).id == att.id
    for call in (
        lambda: world.attach.of_submission(world.token, private.id),
        lambda: world.attach.download(world.token, private.id, att.id),
        lambda: world.add(world.token, private.id, "x.txt", b"hello"),
    ):
        with pytest.raises(NotFoundError):
            call()


def test_a_maintainers_internal_file_is_not_shown_to_the_reporter_and_the_reporter_cannot_make_one(
    world: AttachWorld,
) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, ISSUE)
    world.add(maria, made.id, "internal-notes.txt", b"customer is on a trial\n", visibility="internal")
    shared = world.add(maria, made.id, "fix.txt", b"workaround steps\n")
    assert [a.name for a in world.attach.of_submission(alice, made.id)] == ["fix.txt"]
    assert {a.name for a in world.attach.of_submission(maria, made.id)} == {"internal-notes.txt", "fix.txt"}
    assert world.attach.of_submission(alice, made.id)[0].uploader == "AgentLab maintainer"
    assert world.attach.of_submission(maria, made.id)[0].uploader == "maria"
    with pytest.raises(PolicyBlocked, match="only maintainers"):
        world.add(alice, made.id, "mine.txt", b"x", visibility="internal")
    internal = next(a for a in world.attach.of_submission(maria, made.id) if a.visibility == "internal")
    with pytest.raises(NotFoundError, match="no such attachment"):
        world.attach.download(alice, made.id, internal.id)
    assert shared.visibility == "public"


# ===================================================================================================== limits
def test_the_number_of_files_the_total_size_and_a_repeat_are_limited(make) -> None:  # type: ignore[no-untyped-def]
    w = make(attachments=AttachmentsConfig(max_files=3, max_file_mb=1, max_total_mb=2))
    alice = w.person("alice")
    made = w.file(alice, ISSUE)
    w.add(alice, made.id, "a.txt", b"first\n")
    with pytest.raises(UserError, match="already attached"):
        w.add(alice, made.id, "again.txt", b"first\n")
    with pytest.raises(UserError, match="larger than 1 MB"):
        w.add(alice, made.id, "huge.txt", b"x" * (1024 * 1024 + 1))
    w.add(alice, made.id, "b.txt", b"second " * 100_000)  # about 0.7 MB
    w.add(alice, made.id, "c.txt", b"third " * 100_000)  # about 0.6 MB
    with pytest.raises(PolicyBlocked, match="already has 3 attachments"):
        w.add(alice, made.id, "d.txt", b"fourth\n")
    other = w.file(alice, ISSUE, title="A second problem")
    w.add(alice, other.id, "q.txt", b"x" * 900_000)
    w.add(alice, other.id, "r.txt", b"y" * 1_000_000 + b"1")  # 1.9 MB so far: within 2 MB
    with pytest.raises(PolicyBlocked, match="add up to 2 MB"):
        w.add(alice, other.id, "s.txt", b"z" * 1_000_000 + b"2")


def test_a_reporter_has_a_daily_allowance_of_uploads_and_a_maintainer_has_none(make, clock) -> None:  # type: ignore[no-untyped-def]
    w = make(attachments=AttachmentsConfig(max_uploads_per_day=2, max_files=100))
    alice, maria = w.person("alice"), w.maintainer()
    made = w.file(alice, ISSUE)
    w.add(alice, made.id, "a.txt", b"one\n")
    att = w.add(alice, made.id, "b.txt", b"two\n")
    with pytest.raises(QuotaExceeded):
        w.add(alice, made.id, "c.txt", b"three\n")
    w.attach.remove(alice, made.id, att.id)
    with pytest.raises(QuotaExceeded):
        w.add(alice, made.id, "c.txt", b"three\n")  # removing one does not give the allowance back
    for i in range(4):
        w.add(maria, made.id, f"m{i}.txt", f"maintainer {i}\n".encode())
    clock.advance(hours=25)
    assert w.add(alice, made.id, "c.txt", b"three\n").name == "c.txt"


# ===================================================================================================== scanning
def scanner_script(tmp_path: Path) -> list[str]:
    """A stand-in for a scanner with clamscan's exit codes: 1 for a file that contains EICAR-like text, 2 for 'CRASH'."""
    script = tmp_path / "scan.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys
            data = open(sys.argv[1], "rb").read()
            if b"FAKE-MALWARE-MARKER" in data:
                print(sys.argv[1] + ": Fake.Malware FOUND"); sys.exit(1)
            if b"SCANNER-CRASH" in data:
                print("internal error"); sys.exit(2)
            print(sys.argv[1] + ": OK"); sys.exit(0)
            """
        ),
        encoding="utf-8",
    )
    return [sys.executable, str(script), "{path}"]


def test_a_scanner_that_finds_nothing_is_recorded_as_having_scanned_and_one_that_finds_something_stops_the_file(
    make, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    w = make(attachments=AttachmentsConfig(scanner_command=scanner_script(tmp_path)))
    alice, maria = w.person("alice"), w.maintainer()
    made = w.file(alice, ISSUE)
    ok = w.add(alice, made.id, "fine.txt", b"harmless\n")
    assert ok.scan.status == "clean"
    before = len(stored_files(w.root))
    with pytest.raises(PolicyBlocked, match="malware scanner flagged"):
        w.add(alice, made.id, "bad.txt", b"FAKE-MALWARE-MARKER\n")
    assert len(stored_files(w.root)) == before, "a file the scanner flagged is not kept"
    assert [a.name for a in w.attach.of_submission(alice, made.id)] == ["fine.txt"]
    events = [(e.event, e.detail.get("reason")) for e in w.svc.audit_trail(maria, made.id)]
    assert ("attachment_rejected", "malware") in events


def test_a_scanner_that_fails_is_recorded_as_a_failure_and_not_as_clean(make, tmp_path) -> None:  # type: ignore[no-untyped-def]
    command = scanner_script(tmp_path)
    w = make(attachments=AttachmentsConfig(scanner_command=command))
    alice = w.person("alice")
    made = w.file(alice, ISSUE)
    kept = w.add(alice, made.id, "odd.txt", b"SCANNER-CRASH\n")
    assert kept.scan.status == "error" and "status 2" in kept.scan.detail
    strict = make(attachments=AttachmentsConfig(scanner_command=command, scanner_required=True))
    bob = strict.person("bob")
    other = strict.file(bob, ISSUE)
    with pytest.raises(PolicyBlocked, match="requires a scan"):
        strict.add(bob, other.id, "odd.txt", b"SCANNER-CRASH\n")
    broken = make(attachments=AttachmentsConfig(scanner_command=["/no/such/scanner", "{path}"]))
    carol = broken.person("carol")
    third = broken.file(carol, ISSUE)
    assert broken.add(carol, third.id, "a.txt", b"hello\n").scan.status == "error"


# ===================================================================================================== removal
def test_removing_an_attachment_hides_it_deletes_its_bytes_and_keeps_the_audit_record(world: AttachWorld) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, ISSUE)
    att = world.add(alice, made.id, "run.log", LOG)
    assert stored_files(world.root)
    assert world.attach.remove(alice, made.id, att.id) is True
    assert world.attach.of_submission(alice, made.id) == []
    with pytest.raises(NotFoundError, match="no such attachment"):
        world.attach.download(alice, made.id, att.id)
    assert stored_files(world.root) == [], "the original and its sanitized copy are both gone"
    names = [e.event for e in world.svc.audit_trail(maria, made.id)]
    assert "attachment_added" in names and "attachment_removed" in names
    with world.db.session() as s:
        row = s.execute(text("select status, removed_by_label from submission_attachments")).one()
    assert tuple(row) == ("removed", "alice"), "the row stays: the audit trail refers to it"


def test_the_bytes_stay_while_another_attachment_uses_them(world: AttachWorld) -> None:
    alice = world.person("alice")
    first, second = world.file(alice, ISSUE), world.file(alice, ISSUE, title="A second report of it")
    one = world.add(alice, first.id, "run.log", LOG)
    two = world.add(alice, second.id, "run.log", LOG)  # the same file, attached to another submission
    assert world.attach.remove(alice, first.id, one.id) is False, "the bytes are still used"
    assert world.attach.download(alice, second.id, two.id)[0] == LOG
    assert world.attach.remove(alice, second.id, two.id) is True
    assert stored_files(world.root) == []


def test_a_reporter_cannot_remove_a_file_a_maintainer_attached_but_a_maintainer_can_remove_theirs(
    world: AttachWorld,
) -> None:
    alice, maria, nina = world.person("alice"), world.maintainer("maria"), world.maintainer("nina")
    made = world.file(alice, ISSUE)
    theirs = world.add(maria, made.id, "workaround.txt", b"do this\n")
    with pytest.raises(PolicyBlocked, match="only the person who attached"):
        world.attach.remove(alice, made.id, theirs.id)
    assert world.attach.remove(nina, made.id, theirs.id) is True, "a maintainer may remove anything"


# ===================================================================================================== audit
def test_every_download_is_recorded_without_the_name_or_the_text(world: AttachWorld) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, ISSUE)
    att = world.add(alice, made.id, "customer-acme-passwords.log", LOG)
    world.attach.download(maria, made.id, att.id)
    world.attach.download(maria, made.id, att.id, sanitized=True)
    events = world.svc.audit_trail(maria, made.id)
    downloads = [e for e in events if e.event == "attachment_downloaded"]
    assert sorted((e.actor, e.detail["version"]) for e in downloads) == [("maria", "original"), ("maria", "sanitized")]
    blob = json.dumps([e.model_dump(mode="json") for e in events])
    assert "customer-acme-passwords" not in blob and "jane@example.com" not in blob and OPENAI not in blob
    added = next(e for e in events if e.event == "attachment_added")
    assert added.detail["scan"] == "not_scanned" and added.detail["secrets"] == "redacted"


# ===================================================================================================== storage
def test_the_migration_creates_the_attachment_table_and_can_be_undone(tmp_path: Path) -> None:
    from agentlab.storage.db import Database
    from agentlab.storage.migrate import current_head, downgrade

    db = Database(f"sqlite:///{tmp_path}/m.db")
    db.migrate()
    assert current_head() >= "0004" and "submission_attachments" in set(inspect(db.engine).get_table_names())
    downgrade(db.url, "0003")
    after = set(inspect(db.engine).get_table_names())
    assert "submission_attachments" not in after and "submissions" in after
    db.migrate()
    assert "submission_attachments" in set(inspect(db.engine).get_table_names())


def test_deleting_a_submission_takes_its_attachment_records(world: AttachWorld) -> None:
    alice = world.person("alice")
    made = world.file(alice, ISSUE)
    world.add(alice, made.id, "run.log", LOG)
    with world.db.session() as s:
        s.execute(text("delete from submissions where id = :i"), {"i": made.id})
        assert s.execute(text("select count(*) from submission_attachments")).scalar() == 0


def test_the_configuration_refuses_what_cannot_be_checked() -> None:
    assert "docx" in AttachmentsConfig().allowed_extensions
    with pytest.raises(ValueError, match="cannot check"):
        AttachmentsConfig(allowed_extensions=["png", "exe"])
    with pytest.raises(ValueError, match="max_file_mb"):
        AttachmentsConfig(max_file_mb=50, max_total_mb=10)
    assert FeedbackConfig().attachments.max_files == 10
