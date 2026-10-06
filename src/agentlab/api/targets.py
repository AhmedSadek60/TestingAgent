"""Turning what a request says about a target into something safe to run.

A request can name files on the server (a repository folder, documents) and can carry headers and environment values. Both
are places where a careless API leaks: the first lets a caller read files it should not, the second stores a secret in a
database row that anyone with read access can see. This module closes both."""

from __future__ import annotations

import re
from pathlib import Path

from agentlab.api.security import confine, safe_filename
from agentlab.api.state import ApiState
from agentlab.core.errors import NotFoundError, PolicyBlocked, UserError
from agentlab.core.models import TargetSpec
from agentlab.security.redactor import SENSITIVE_KEYS, get_redactor

UPLOAD = "upload:"
SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "x-auth-token",
    "x-access-token",
    "x-csrf-token",
}
TOKEN_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
SECRET_WORDS = {
    "secret", "token", "password", "passwd", "pwd", "authorization", "auth", "cookie", "credential", "credentials",
    "bearer", "signature", "apikey", "passphrase",
}  # fmt: skip
SECRET_PAIRS = {
    ("api", "key"),
    ("private", "key"),
    ("access", "key"),
    ("secret", "key"),
    ("auth", "key"),
    ("signing", "key"),
}


def names_a_secret(name: str) -> bool:
    """Whether a header or environment variable is named like something that holds a secret: ``X-Client-Secret``,
    ``clientSecret``, ``OPENAI_API_KEY``, ``DB_PASSWORD``. Whole words only, so ``X-Author`` and ``X-Idempotency-Key`` are not."""
    words = [w.lower() for w in WORDS.findall(name)]
    return bool(SECRET_WORDS & set(words)) or any(pair in SECRET_PAIRS for pair in zip(words, words[1:], strict=False))


# =========================================================================================== uploaded files
def upload_path(st: ApiState, sha256: str, name: str) -> Path:
    """Where an upload lives: ``uploads/<sha256>/<safe name>``. The content decides the folder, so one file is stored once."""
    return st.uploads_dir / sha256 / safe_filename(name)


def resolve_upload(st: ApiState, ref: str) -> Path:
    """The file an ``upload:<document id>`` reference names (the newest version of that document)."""
    ident = ref[len(UPLOAD) :].strip()
    if not re.fullmatch(r"[0-9a-fA-F-]{8,40}", ident):
        raise UserError(f"'{ref[:60]}' is not a valid upload reference (expected upload:<id> from POST /documents)")
    for project in st.store.list_projects():
        for doc in st.store.list_documents(project["id"]):
            if doc["id"] == ident or str(doc["id"]).startswith(ident):
                versions = sorted(doc.get("versions") or [], key=lambda v: v["version"])
                if not versions:
                    break
                path = upload_path(st, versions[-1]["sha256"], doc["name"])
                if not path.is_file():
                    raise NotFoundError(f"the file of upload '{ident}' is no longer on the server; upload it again")
                return path
    raise NotFoundError(f"upload '{ident}' does not exist (see GET /documents)")


def server_path(st: ApiState, raw: str, *, what: str) -> str:
    """An ``upload:`` reference or a path inside an allowed folder, as an absolute path."""
    if raw.startswith(UPLOAD):
        return str(resolve_upload(st, raw))
    try:
        return str(confine(raw, [*st.allowed_roots, st.uploads_dir], what=what))
    except PolicyBlocked:
        if st.allowed_roots:
            raise
        raise PolicyBlocked(
            f"{what} names a path on the server, and this server allows none (server.allowed_paths is empty). "
            "Upload the file with POST /documents and use its upload:<id> reference instead"
        ) from None


# ================================================================================================== a target
def prepare_target(st: ApiState, spec: TargetSpec) -> TargetSpec:
    """A copy of ``spec`` that is safe to run: no secret sent inline, every server path confined to the folders allowed."""
    reject_inline_secrets(spec)
    out = spec.model_copy(deep=True)
    if out.repository is not None:
        if out.repository.path:
            out.repository.path = server_path(st, out.repository.path, what="repository.path")
        if out.repository.archive:
            out.repository.archive = server_path(st, out.repository.archive, what="repository.archive")
    out.documents = [server_path(st, d, what="a document") for d in out.documents]
    if out.llm is not None:
        st.services.config.provider(
            out.llm.provider
        )  # an unknown provider is a mistake of the request, not a failed run
    return out


def check_target(st: ApiState, spec: TargetSpec) -> None:
    """Validate a target for storing (nothing is resolved or copied)."""
    prepare_target(st, spec)


def reject_inline_secrets(spec: TargetSpec) -> None:
    """Secrets belong in the encrypted credential store, named by ``auth_credential``. A header or an environment value that
    carries one would be written to the database with the target definition."""
    redactor = get_redactor()
    bad: list[str] = []

    def looks_secret(value: str) -> bool:
        return bool(redactor.redact_text(value)[1])

    groups = (
        ("api.headers", spec.api.headers if spec.api else {}),
        ("mcp.headers", spec.mcp.headers if spec.mcp else {}),
    )
    for where, headers in groups:
        for name, value in headers.items():
            if not TOKEN_NAME.match(name):
                raise UserError(f"{where}: '{name[:40]}' is not a valid header name")
            if "\r" in value or "\n" in value or "\x00" in value:
                raise UserError(f"{where}.{name}: a header value cannot contain a line break")
            if (
                name.lower() in SENSITIVE_HEADERS
                or SENSITIVE_KEYS.match(name)
                or names_a_secret(name)
                or looks_secret(value)
            ):
                bad.append(f"{where}.{name}")
    envs = (("command.env", spec.command.env if spec.command else {}), ("mcp.env", spec.mcp.env if spec.mcp else {}))
    for where, env in envs:
        for name, value in env.items():
            if SENSITIVE_KEYS.match(name) or names_a_secret(name) or looks_secret(value):
                bad.append(f"{where}.{name}")
    if bad:
        raise PolicyBlocked(
            f"secrets must not be sent inline ({', '.join(bad[:5])}). Store the secret with POST /credentials and name it "
            "in `auth_credential`"
        )
