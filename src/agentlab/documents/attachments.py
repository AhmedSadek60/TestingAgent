"""Attachments a test sends to a target (spec sections 20 and 23).

An attachment is either a ``gen://`` recipe (made at run time, see :mod:`agentlab.documents.generated`) or a file from the
fixture directory: never an arbitrary path on the host. Shared by the conversation engine and the browser engine.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from agentlab.core.errors import PolicyBlocked, UserError
from agentlab.core.models import Attachment
from agentlab.documents.generated import generate, is_generated
from agentlab.evaluation.context import PlaceholderResolver

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

TEXT_SUFFIXES = {".txt", ".md", ".py", ".html", ".htm", ".csv", ".json", ".yaml", ".yml", ".xml"}


def load_attachment(ref: str, base: Path | None, resolver: PlaceholderResolver | None = None) -> Attachment:
    """An attachment is either a ``gen://`` recipe (made at run time, see ``documents.generated``) or a file from the
    fixture directory: never an arbitrary host path. Canary placeholders are resolved in the reference and in the
    text of text-like files, so a planted instruction carries this run's marker."""
    ref = resolver.resolve(ref) if resolver else ref
    if is_generated(ref):
        try:
            made = generate(ref)
        except ValueError as exc:
            raise UserError(str(exc)) from exc
        return Attachment(name=made.name, media_type=made.media_type, content_b64=base64.b64encode(made.data).decode())
    root = (base or Path.cwd()).resolve()
    path = (root / ref).resolve()
    if root not in path.parents and path != root:
        raise PolicyBlocked(f"attachment '{ref}' escapes the fixtures directory")
    if not path.is_file():
        raise UserError(f"attachment '{ref}' not found under {root}")
    data = path.read_bytes()
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise UserError(f"attachment '{ref}' is larger than {MAX_ATTACHMENT_BYTES} bytes")
    if resolver and path.suffix.lower() in TEXT_SUFFIXES:
        try:
            data = resolver.resolve(data.decode("utf-8")).encode("utf-8")
        except UnicodeDecodeError:
            pass  # not text after all: attached as it is
    mt = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Attachment(name=path.name, media_type=mt, content_b64=base64.b64encode(data).decode())
