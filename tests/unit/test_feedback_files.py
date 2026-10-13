"""Looking at an uploaded file: its type from its content, what a hostile archive looks like, and a safe name."""

from __future__ import annotations

import io
import struct
import zipfile

import pytest

from agentlab.core.config import AttachmentsConfig
from agentlab.core.errors import PolicyBlocked, UserError
from agentlab.feedback.files import display_name, extension_of, inspect_archive, inspect_upload

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def make_zip(entries: dict[str, bytes], *, compress: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


CFG = AttachmentsConfig()


# ===================================================================================== what a file is
@pytest.mark.parametrize(
    ("name", "data", "media", "kind"),
    [
        ("shot.png", PNG, "image/png", "image"),
        ("photo.JPG", JPEG, "image/jpeg", "image"),
        ("photo.jpeg", JPEG, "image/jpeg", "image"),
        ("pic.webp", WEBP, "image/webp", "image"),
        ("spec.pdf", PDF, "application/pdf", "pdf"),
        ("notes.txt", b"hello\nworld\n", "text/plain", "text"),
        ("readme.md", "# Titre — naïve café\n".encode(), "text/markdown", "text"),
        ("rows.csv", b"a,b\n1,2\n", "text/csv", "text"),
        ("data.json", b'{"a": 1}', "application/json", "text"),
        ("lines.jsonl", b'{"a": 1}\n{"a": 2}\n', "application/x-ndjson", "text"),
        ("conf.yaml", b"key: value\n", "application/yaml", "text"),
        ("conf.yml", b"key: value\n", "application/yaml", "text"),
        ("run.log", b"\x1b[31mERROR\x1b[0m boom\n", "text/plain", "text"),
        ("bundle.zip", make_zip({"a.log": b"line\n"}), "application/zip", "archive"),
    ],
)
def test_a_file_is_what_its_content_says_and_the_media_type_is_ours_not_the_browsers(
    name: str, data: bytes, media: str, kind: str
) -> None:
    found = inspect_upload(name, data, CFG)
    assert (found.media_type, found.kind) == (media, kind) and found.size == len(data)
    assert found.text_like is (kind == "text")


def test_a_text_file_with_a_byte_order_mark_is_read_without_it() -> None:
    assert inspect_upload("a.txt", b"\xef\xbb\xbfhello", CFG).text == "hello"


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("shot.png", b"MZ\x90\x00 an executable pretending", "not a PNG"),
        ("shot.png", JPEG, "not a PNG"),
        ("photo.jpg", PNG, "not a JPEG"),
        ("pic.webp", PNG, "not a WebP"),
        ("spec.pdf", b"<html><script>alert(1)</script>", "not a PDF"),
        ("notes.txt", PNG, "binary data|not UTF-8"),
        ("notes.txt", "naïve".encode("utf-16"), "not UTF-8"),
        ("notes.txt", b"hello\x00world", "binary data"),
        ("data.json", b"\x00\x01\x02", "binary data"),
        ("bundle.zip", b"this is not a zip at all", "not a ZIP"),
        ("doc.docx", make_zip({"readme.txt": b"hi"}), "not a Word document"),
        ("empty.txt", b"", "empty"),
    ],
)
def test_a_file_that_is_not_what_its_name_says_is_refused_with_the_reason(name: str, data: bytes, message: str) -> None:
    with pytest.raises(UserError, match=message):
        inspect_upload(name, data, CFG)


HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 32
GIF = b"GIF89a\x01\x00\x01\x00" + b"\x00" * 16


@pytest.mark.parametrize(
    ("name", "data", "says", "advises"),
    [
        # another type AgentLab accepts: name it as what it is
        ("photo.jpg", PNG, "its content is a PNG image, whatever its name says", "Rename it to '.png'"),
        ("photo.jpeg", WEBP, "its content is a WebP image", "Rename it to '.webp'"),
        ("photo.png", JPEG, "its content is a JPEG image", "Rename it to '.jpg'"),
        ("photo.jpg", PDF, "its content is a PDF document", "Rename it to '.pdf'"),
        # a kind of image AgentLab does not take: say what it is, and what to save it as
        (
            "photo.jpg",
            HEIC,
            "its content is a HEIC image (the format many phones keep photos in)",
            "Save it again as a JPEG or PNG",
        ),
        ("photo.jpg", GIF, "its content is a GIF image", "Save it again as a JPEG or PNG"),
        ("photo.jpg", b"BM" + b"\x00" * 40, "its content is a BMP image", "Save it again as a JPEG or PNG"),
        # nothing worth naming
        (
            "photo.jpg",
            b"<html><body>hi</body></html>",
            "does not look like an image at all",
            "Open it in an image viewer",
        ),
        ("photo.jpg", b"\x00\x01\x02\x03" * 8, "does not look like an image at all", "save it again as a JPEG or PNG"),
    ],
)
def test_a_refused_image_says_what_it_really_is_and_what_to_do(name: str, data: bytes, says: str, advises: str) -> None:
    with pytest.raises(UserError) as refused:
        inspect_upload(name, data, CFG)
    message = str(refused.value)
    assert message.startswith("the file is not a "), message
    assert says in message and advises in message, message


def test_a_refused_image_is_not_told_to_use_a_type_the_server_does_not_take() -> None:
    narrow = AttachmentsConfig(allowed_extensions=["jpg", "png", "txt"])
    with pytest.raises(UserError) as refused:
        inspect_upload("photo.jpg", WEBP, narrow)
    assert "WebP image" in str(refused.value) and "Rename it" not in str(refused.value)
    assert "Save it again as a JPEG or PNG" in str(refused.value)


@pytest.mark.parametrize(
    "name", ["tool.exe", "page.html", "image.svg", "script.js", "macro.docm", "noextension", "x.PHP"]
)
def test_a_type_that_is_not_on_the_list_is_refused_and_the_list_is_shown(name: str) -> None:
    with pytest.raises(PolicyBlocked, match="cannot be attached \\(allowed: "):
        inspect_upload(name, b"content", CFG)


def test_an_owner_can_switch_a_known_type_off() -> None:
    narrow = AttachmentsConfig(allowed_extensions=["png", "txt"])
    assert inspect_upload("a.png", PNG, narrow).kind == "image"
    with pytest.raises(PolicyBlocked, match="not accepted on this server"):
        inspect_upload("a.pdf", PDF, narrow)


def test_a_docx_is_a_zip_with_a_document_part() -> None:
    docx = make_zip(
        {"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w:document/>", "word/media/a.png": PNG}
    )
    found = inspect_upload("report.docx", docx, CFG)
    assert found.kind == "office" and found.archive["entries"] == 3


# ========================================================================================== archives
def test_an_archive_is_summarised_without_being_extracted() -> None:
    zipped = make_zip({"logs/app.log": b"a" * 100, "logs/worker.log": b"b" * 200, "env.json": b"{}"})
    summary = inspect_archive(zipped, CFG)
    assert summary["entries"] == 3 and summary["expanded_bytes"] == 302 and summary["stored_bytes"] == len(zipped)
    assert summary["names"] == ["app.log", "worker.log", "env.json"] and summary["names_truncated"] is False


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ("../../etc/passwd", "climbs out"),
        ("/etc/cron.d/job", "absolute path"),
        ("C:/Windows/system32/x.dll", "absolute path"),
        ("..\\..\\windows\\x.txt", "climbs out"),
        ("tools/setup.exe", "program or script"),
        ("run.sh", "program or script"),
        ("inner.zip", "archive inside the archive"),
        ("bad\x01name.txt", "control character"),
    ],
)
def test_an_archive_with_a_dangerous_entry_is_refused_and_names_it(entry: str, reason: str) -> None:
    with pytest.raises(PolicyBlocked, match=reason):
        inspect_archive(make_zip({"ok.log": b"fine", entry: b"x"}), CFG)


def test_a_symbolic_link_inside_an_archive_is_refused() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        link = zipfile.ZipInfo("shortcut")
        link.create_system = 3
        link.external_attr = (0o120777) << 16
        z.writestr(link, "/etc/passwd")
    with pytest.raises(PolicyBlocked, match="symbolic link"):
        inspect_archive(buf.getvalue(), CFG)


def test_an_encrypted_entry_is_refused_because_it_cannot_be_inspected() -> None:
    data = bytearray(make_zip({"secret.txt": b"hidden"}))
    # set bit 0 of the general purpose flags in the local header and the central directory entry
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = data.find(signature)
        flags = struct.unpack_from("<H", data, at + offset)[0] | 1
        struct.pack_into("<H", data, at + offset, flags)
    with pytest.raises(PolicyBlocked, match="encryption"):
        inspect_archive(bytes(data), CFG)


def test_too_many_entries_and_too_much_expansion_are_refused() -> None:
    many = make_zip({f"f{i}.log": b"x" for i in range(30)})
    with pytest.raises(PolicyBlocked, match="lists 30 files"):
        inspect_archive(many, AttachmentsConfig(zip_max_entries=10))
    big = make_zip({"a.log": b"0" * (3 * 1024 * 1024)})
    with pytest.raises(PolicyBlocked, match="would expand to 3 MB"):
        inspect_archive(big, AttachmentsConfig(zip_max_expanded_mb=2, zip_max_ratio=10_000))


def test_an_archive_bomb_is_recognised_by_its_ratio_and_a_small_repetitive_file_is_not() -> None:
    bomb = make_zip({"zeros.log": b"\x00" * (20 * 1024 * 1024)})
    assert len(bomb) < 50_000
    with pytest.raises(PolicyBlocked, match="bomb"):
        inspect_archive(bomb, AttachmentsConfig(zip_max_expanded_mb=100))
    small = make_zip({"padding.log": b"\x00" * 50_000})
    assert inspect_archive(small, CFG)["entries"] == 1, "a small file may compress well without being a bomb"


def test_a_damaged_archive_is_refused() -> None:
    zipped = make_zip({"a.log": b"hello world" * 100})
    with pytest.raises(UserError, match="damaged"):
        inspect_archive(zipped[: len(zipped) // 2] + b"PK\x03\x04", CFG)


# ======================================================================================= names
@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("report.pdf", "report.pdf"),
        ("C:\\Users\\jane\\Desktop\\report.pdf", "report.pdf"),
        ("../../../etc/passwd", "passwd"),
        ("name\r\nX-Injected: yes.txt", "nameX-Injected: yes.txt"),
        ("invoice\u202egpj.exe", "invoicegpj.exe"),
        ("  ..hidden.txt  ", "hidden.txt"),
        ("", "attachment"),
        ("\x00\x01", "attachment"),
        ("x" * 300 + ".png", "x" * 116 + ".png"),
        ("y" * 300, "y" * 120),
    ],
)
def test_a_file_name_is_reduced_to_something_safe_to_show(raw: str, shown: str) -> None:
    assert display_name(raw) == shown


def test_the_extension_is_taken_from_the_safe_name() -> None:
    assert (
        extension_of("Archive.TAR.GZ") == "gz" and extension_of("../x/y.PNG") == "png" and extension_of("noext") == ""
    )
