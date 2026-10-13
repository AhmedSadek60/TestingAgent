"""Looking at an uploaded file: what it is, whether it is what it says it is, and what an archive lists.

Nothing here runs, opens, renders or extracts a file. The checks are on its first bytes, its text and an archive's
directory, and a file that fails one is refused with the reason. A file name is only ever a label: where the bytes are kept
is decided by their hash."""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from agentlab.core.config import AttachmentsConfig
from agentlab.core.errors import PolicyBlocked, UserError

MiB = 1024 * 1024

#: extension -> (the media type it is stored and described as, how it is checked)
TYPES: dict[str, tuple[str, str]] = {
    "png": ("image/png", "image"),
    "jpg": ("image/jpeg", "image"),
    "jpeg": ("image/jpeg", "image"),
    "webp": ("image/webp", "image"),
    "pdf": ("application/pdf", "pdf"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "office"),
    "zip": ("application/zip", "archive"),
    "txt": ("text/plain", "text"),
    "md": ("text/markdown", "text"),
    "csv": ("text/csv", "text"),
    "json": ("application/json", "text"),
    "jsonl": ("application/x-ndjson", "text"),
    "yaml": ("application/yaml", "text"),
    "yml": ("application/yaml", "text"),
    "log": ("text/plain", "text"),
}
#: Entries that must not be in an archive of diagnostics: programs, and archives inside archives.
EXECUTABLE = frozenset(
    [
        "exe",
        "dll",
        "bat",
        "cmd",
        "com",
        "scr",
        "msi",
        "ps1",
        "vbs",
        "vbe",
        "wsf",
        "js",
        "jse",
        "jar",
        "sh",
        "bash",
        "app",
        "apk",
        "dmg",
        "so",
        "dylib",
        "pif",
        "lnk",
        "reg",
        "hta",
    ]
)
NESTED = frozenset(["zip", "jar", "war", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "cab", "iso"])
_BIDI = re.compile("[‪-‮⁦-⁩‎‏]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass
class Inspected:
    """What an upload turned out to be."""

    extension: str
    media_type: str
    kind: str  # image | pdf | office | archive | text
    size: int
    text: str | None = None  # the decoded text, for a text file
    archive: dict[str, Any] = field(default_factory=dict)

    @property
    def text_like(self) -> bool:
        return self.kind == "text"


def display_name(raw: str) -> str:
    """A file name that is safe to show and to put in a header: its last path component, without control characters or
    direction overrides (which can make ``invoice-exe.pdf`` read as something else), at most 120 characters."""
    name = unicodedata.normalize("NFC", raw or "")
    name = _BIDI.sub("", _CONTROL.sub("", name))
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip().strip(".")
    if len(name) > 120:  # shorten the stem, not the extension: the extension is what says what the file is
        stem, dot, extension = name.rpartition(".")
        keep = extension[:16] if dot and 0 < len(extension) <= 16 else ""
        name = (stem[: 120 - len(keep) - 1] + "." + keep) if keep else name[:120]
    return (name or "attachment").strip()


def extension_of(name: str) -> str:
    return PurePosixPath(display_name(name)).suffix.lower().lstrip(".")


def inspect_upload(name: str, data: bytes, config: AttachmentsConfig) -> Inspected:
    """Decide whether ``data`` is the kind of file its name says, or refuse it with the reason."""
    extension = extension_of(name)
    if extension not in TYPES:
        allowed = ", ".join(sorted(config.allowed_extensions))
        raise PolicyBlocked(f"files of type '.{extension or 'unknown'}' cannot be attached (allowed: {allowed})")
    if extension not in config.allowed_extensions:
        allowed = ", ".join(sorted(config.allowed_extensions))
        raise PolicyBlocked(f"'.{extension}' files are not accepted on this server (allowed: {allowed})")
    if not data:
        raise UserError("the file is empty")
    media_type, kind = TYPES[extension]
    inspected = Inspected(extension=extension, media_type=media_type, kind=kind, size=len(data))
    if kind == "image":
        _image(extension, data)
    elif kind == "pdf":
        if data[:1024].find(b"%PDF-") == -1:
            raise UserError("the file is not a PDF document (it has no PDF header)")
    elif kind in ("archive", "office"):
        inspected.archive = inspect_archive(data, config, office=kind == "office")
    else:
        inspected.text = _text(data, extension)
    return inspected


def _image(extension: str, data: bytes) -> None:
    ok = {
        "png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": data[:3] == b"\xff\xd8\xff",
        "jpeg": data[:3] == b"\xff\xd8\xff",
        "webp": data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    }[extension]
    if not ok:
        label = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WebP"}[extension]
        raise UserError(f"the file is not a {label} image (its content does not match its name)")


def _text(data: bytes, extension: str) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UserError(
            f"the '.{extension}' file is not UTF-8 text: save it as UTF-8, or attach it in a ZIP archive"
        ) from exc
    if "\x00" in text:
        raise UserError(f"the '.{extension}' file contains binary data, so it is not a text file")
    return text


def _entry_problems(info: zipfile.ZipInfo) -> list[str]:
    name = info.filename
    normal = name.replace("\\", "/")
    parts = PurePosixPath(normal).parts
    problems = []
    if _CONTROL.search(name):
        problems.append("a control character in its name")
    if normal.startswith("/") or re.match(r"^[A-Za-z]:", normal):
        problems.append("an absolute path")
    if ".." in parts:
        problems.append("a path that climbs out of its folder")
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        problems.append("a symbolic link")
    if info.flag_bits & 0x1:
        problems.append("encryption (it cannot be inspected)")
    extension = PurePosixPath(normal).suffix.lower().lstrip(".")
    if extension in EXECUTABLE:
        problems.append(f"a program or script ('.{extension}')")
    if extension in NESTED:
        problems.append(f"an archive inside the archive ('.{extension}')")
    return problems


def inspect_archive(data: bytes, config: AttachmentsConfig, *, office: bool = False) -> dict[str, Any]:
    """The summary of an archive, or a refusal. The archive is never extracted: this reads its directory only, and the
    sizes it declares are what an extractor would be asked to produce."""
    if data[:4] not in (b"PK\x03\x04", b"PK\x05\x06"):
        raise UserError("the file is not a ZIP archive (it has no ZIP header)")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise UserError("the archive is damaged and cannot be read") from exc
    if len(infos) > config.zip_max_entries:
        raise PolicyBlocked(
            f"the archive lists {len(infos)} files; at most {config.zip_max_entries} are accepted "
            "(feedback.attachments.zip_max_entries)"
        )
    expanded = 0
    flagged: list[str] = []
    for info in infos:
        expanded += info.file_size
        problems = [] if office else _entry_problems(info)
        if info.file_size >= MiB and info.file_size / max(1, info.compress_size) > config.zip_max_ratio:
            problems.append("an expansion ratio that looks like an archive bomb")
        if problems:
            flagged.append(f"{display_name(info.filename)[:60]}: {', '.join(problems)}")
    if flagged:
        shown = "; ".join(flagged[:5]) + (f"; and {len(flagged) - 5} more" if len(flagged) > 5 else "")
        raise PolicyBlocked(f"the archive holds entries that are not accepted: {shown}")
    if expanded > config.zip_max_expanded_mb * MiB:
        raise PolicyBlocked(
            f"the archive would expand to {expanded // MiB} MB; at most {config.zip_max_expanded_mb} MB is accepted "
            "(feedback.attachments.zip_max_expanded_mb)"
        )
    ratio = expanded / max(1, len(data))
    if expanded > 10 * MiB and ratio > config.zip_max_ratio:
        raise PolicyBlocked(
            f"the archive expands {ratio:.0f} times its size, which looks like an archive bomb "
            f"(feedback.attachments.zip_max_ratio is {config.zip_max_ratio})"
        )
    names = [i.filename for i in infos]
    if office and not ({"[Content_Types].xml", "word/document.xml"} <= set(names)):
        raise UserError("the file is not a Word document (its package has no document part)")
    return {
        "entries": len(infos),
        "expanded_bytes": expanded,
        "stored_bytes": len(data),
        "names": [display_name(n)[:80] for n in names[:20]],
        "names_truncated": len(names) > 20,
    }
