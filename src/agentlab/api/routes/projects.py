"""Projects, targets, credentials, documents and discovery: everything that exists before a run."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile

from agentlab.api.routes.common import RESPONSES, require_auth
from agentlab.api.schemas import (
    CredentialCreate,
    CredentialOut,
    CredentialRotate,
    DiscoverRequest,
    DiscoverResponse,
    DocumentOut,
    DocumentSummary,
    ProjectCreate,
    ProjectOut,
    TargetCreate,
    TargetOut,
)
from agentlab.api.security import safe_filename
from agentlab.api.state import state_of
from agentlab.api.targets import check_target, prepare_target, upload_path
from agentlab.core.errors import NotFoundError, PolicyBlocked, UserError
from agentlab.core.ids import sha256_hex
from agentlab.core.models import TargetSpec
from agentlab.documents.analyzer import DocumentAnalyzer
from agentlab.security.credentials import CredentialProfile

router = APIRouter(dependencies=[Depends(require_auth)], responses=RESPONSES)

FIXED_FIELDS = {"bearer": ["token"], "oauth_token": ["token"], "api_key": ["key"], "basic": ["username", "password"]}
FIELD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}$")
UPLOAD_SUFFIXES = {
    ".pdf", ".docx", ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".zip", ".tar", ".gz", ".tgz",
}  # fmt: skip
ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz"}


# ================================================================================================== projects
def _project_out(row: dict[str, Any]) -> ProjectOut:
    return ProjectOut(
        id=row["id"],
        name=row["name"],
        description=row.get("description") or "",
        objective=row.get("objective") or "",
        created_at=row["created_at"],
    )


@router.post(
    "/projects",
    response_model=ProjectOut,
    status_code=201,
    tags=["Projects"],
    summary="Create a project",
    description="A project groups targets, documents and runs. Names are unique.",
)
def create_project(body: ProjectCreate, request: Request) -> Any:
    return _project_out(state_of(request).store.create_project(body.name, body.description, body.objective))


@router.get(
    "/projects",
    response_model=list[ProjectOut],
    tags=["Projects"],
    summary="List projects",
    description="Every project, oldest first.",
)
def list_projects(request: Request) -> Any:
    return [_project_out(row) for row in state_of(request).store.list_projects()]


# =================================================================================================== targets
def _target_out(row: dict[str, Any], spec: TargetSpec) -> TargetOut:
    return TargetOut(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        kind=row["kind"],
        target_version=row.get("target_version"),
        spec=spec,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.post(
    "/targets",
    response_model=TargetOut,
    status_code=201,
    tags=["Targets"],
    summary="Register a target",
    description=(
        "Store the definition of an agent to test (the document `agentlab test --target` reads). Registering a target "
        "again under the same name in the same project updates it. Secrets must not be sent inline: store them with "
        "`POST /credentials` and name them in `auth_credential`. Paths on the server are accepted only under "
        "`server.allowed_paths`; use `upload:<id>` for files sent with `POST /documents`."
    ),
)
def create_target(body: TargetCreate, request: Request) -> Any:
    st = state_of(request)
    check_target(st, body.spec)
    project = st.store.ensure_project(body.project)
    row = st.store.add_target(project["id"], body.spec)
    return _target_out(row, body.spec)


@router.get(
    "/targets",
    response_model=list[TargetOut],
    tags=["Targets"],
    summary="List targets",
    description="Stored targets, optionally of one project.",
)
def list_targets(request: Request, project: str | None = None) -> Any:
    st = state_of(request)
    project_id = st.store.get_project(project)["id"] if project else None
    out = []
    for row in st.store.list_targets(project_id):
        out.append(_target_out(row, TargetSpec.model_validate(row["spec"])))
    return out


@router.get(
    "/targets/{target_id}",
    response_model=TargetOut,
    tags=["Targets"],
    summary="Get a target",
    description="One stored target with its definition.",
)
def get_target(target_id: str, request: Request) -> Any:
    row, spec = state_of(request).store.get_target(target_id)
    return _target_out(row, spec)


@router.get(
    "/targets/{target_id}/profile",
    response_model=DiscoverResponse,
    tags=["Targets"],
    summary="Get what discovery learned about a target",
    description="The most recent agent profile stored for the target (from `POST /discover` or a run).",
)
def get_target_profile(target_id: str, request: Request) -> Any:
    st = state_of(request)
    st.store.get_target(target_id)
    profile = st.store.latest_profile(target_id)
    if profile is None:
        raise NotFoundError(f"target '{target_id}' has not been discovered yet (POST /discover)")
    return DiscoverResponse(target_id=target_id, profile=profile, warnings=[])


# ============================================================================================== credentials
def _credential_out(request: Request, name: str) -> CredentialOut:
    mgr = state_of(request).services.credentials
    p = mgr.get_profile(name)
    return CredentialOut(
        name=p.name,
        kind=p.kind,
        description=p.description,
        scopes=p.scopes,
        header_name=p.header_name,
        expires_at=p.expires_at,
        secret_version=p.secret_version,
        fields=mgr.field_names(name),
        references=p.references,
        test_only=p.test_only,
    )


def _check_secret_values(values: dict[str, str]) -> None:
    for field, value in values.items():
        if not FIELD_NAME.match(field) and not HEADER_NAME.match(field):
            raise UserError(f"'{field[:40]}' is not a valid field name")
        if not value:
            raise UserError(f"{field} must not be empty")
        if len(value) > 64_000:
            raise UserError(f"{field} is too long")
        if field != "state" and any(c in value for c in "\r\n\x00"):
            raise UserError(f"{field} cannot contain a line break (it would be sent in a header)")


def _check_credential(body: CredentialCreate) -> None:
    if not (body.scopes or body.unscoped or body.references):
        raise UserError(
            "give `scopes` (the hosts this credential may be sent to), or `unscoped: true` to allow any host the "
            "target definition names"
        )
    _check_secret_values(body.secrets)
    kind = body.kind
    given = set(body.secrets) | set(body.references)
    if kind in FIXED_FIELDS:
        missing = [f for f in FIXED_FIELDS[kind] if f not in given]
        extra = sorted(given - set(FIXED_FIELDS[kind]))
        if missing:
            raise UserError(
                f"a {kind} credential needs: {', '.join(FIXED_FIELDS[kind])} (missing {', '.join(missing)})"
            )
        if extra:
            raise UserError(f"a {kind} credential holds only {', '.join(FIXED_FIELDS[kind])}, not {', '.join(extra)}")
    elif kind in {"headers", "cookies"}:
        if not given:
            raise UserError(f"a {kind} credential needs at least one field")
        bad = [f for f in given if not HEADER_NAME.match(f)]
        if bad:
            raise UserError(f"'{bad[0][:40]}' is not a valid {'header' if kind == 'headers' else 'cookie'} name")
    elif kind == "browser_state":
        if body.references or set(body.secrets) != {"state"}:
            raise UserError("a browser_state credential holds exactly one secret, `state` (a Playwright storage state)")
        try:
            doc = json.loads(body.secrets["state"])
        except ValueError as exc:
            raise UserError("`state` must be the JSON of a Playwright storage state") from exc
        if not isinstance(doc, dict) or not ({"cookies", "origins"} & set(doc)):
            raise UserError("`state` must be a Playwright storage state (an object with cookies and/or origins)")
    elif kind == "env" and not body.references:
        raise UserError("an env credential needs `references` (field -> env:NAME)")
    if body.header_name and not HEADER_NAME.match(body.header_name):
        raise UserError("header_name is not a valid header name")


@router.post(
    "/credentials",
    response_model=CredentialOut,
    status_code=201,
    tags=["Credentials"],
    summary="Store a test credential",
    description=(
        "Store credentials for an account you are allowed to test with, encrypted at rest and scoped to the hosts they may "
        "be sent to. The secret values are accepted here and are **never returned** by any endpoint, written to a log, a "
        "trace or a report."
    ),
)
def create_credential(body: CredentialCreate, request: Request) -> Any:
    mgr = state_of(request).services.credentials
    mgr.reload()
    _check_credential(body)
    profile = CredentialProfile(
        name=body.name,
        kind=body.kind,
        description=body.description,
        scopes=list(body.scopes),
        header_name=body.header_name,
        expires_at=body.expires_at,
        references=dict(body.references),
    )
    mgr.add(profile, dict(body.secrets) or None)
    return _credential_out(request, body.name)


@router.get(
    "/credentials",
    response_model=list[CredentialOut],
    tags=["Credentials"],
    summary="List credential profiles",
    description="Names, kinds, scopes and field names. Values are never shown.",
)
def list_credentials(request: Request) -> Any:
    mgr = state_of(request).services.credentials
    mgr.reload()
    return [_credential_out(request, p["name"]) for p in mgr.list_profiles()]


@router.post(
    "/credentials/{name}/rotate",
    response_model=CredentialOut,
    tags=["Credentials"],
    summary="Replace the secret values of a credential",
    description="Stores new values under the same name and scope and bumps its secret version.",
)
def rotate_credential(name: str, body: CredentialRotate, request: Request) -> Any:
    mgr = state_of(request).services.credentials
    mgr.reload()
    if not mgr.has(name):
        raise NotFoundError(f"credential '{name}' is not stored")
    _check_secret_values(body.secrets)
    mgr.rotate(name, dict(body.secrets))
    return _credential_out(request, name)


@router.delete(
    "/credentials/{name}",
    status_code=204,
    tags=["Credentials"],
    summary="Delete a credential",
    description="Removes the profile and its secret values. Targets that name it will have their authenticated tests BLOCKED.",
)
def delete_credential(name: str, request: Request) -> Response:
    mgr = state_of(request).services.credentials
    mgr.reload()
    if not mgr.has(name):
        raise NotFoundError(f"credential '{name}' is not stored")
    mgr.remove(name)
    return Response(status_code=204)


# ================================================================================================== documents
def _summary(doc: Any) -> DocumentSummary:
    return DocumentSummary(
        pages=int(doc.pages or 0),
        text_chars=int(doc.text_chars or 0),
        knowledge_items=len(doc.items),
        headings=len(doc.headings),
        tables=len(doc.tables),
        warnings=list(doc.warnings)[:20],
        injection_indicators=[str(i) for i in doc.injection_indicators][:20],
        hidden_content=len(doc.hidden_content),
        unsupported=doc.unsupported,
    )


@router.post(
    "/documents",
    response_model=DocumentOut,
    status_code=201,
    tags=["Documents"],
    summary="Upload a document",
    description=(
        "Upload a file to use with a target: a knowledge-base document (pdf, docx, md, txt, csv, html, images), a "
        "repository archive (zip, tar, tgz) or a file of your own test cases (yaml, json). The file is analysed for a "
        "preview and kept under a name made from its content. Everything in it is treated as untrusted data. Use the "
        "returned `ref` (`upload:<id>`) in a target's `documents`, `repository.archive` or a run's `user_test_files`."
    ),
)
async def upload_document(
    request: Request,
    file: Annotated[UploadFile, File(description="The file to upload")],
    project: Annotated[str, Form(description="Project name or id; created if it does not exist")] = "default",
) -> Any:
    st = state_of(request)
    name = safe_filename(file.filename or "upload")
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise PolicyBlocked(
            f"files of type '{suffix or 'unknown'}' cannot be uploaded (allowed: {', '.join(sorted(UPLOAD_SUFFIXES))})"
        )
    spool = tempfile.NamedTemporaryFile(prefix="agentlab-upload-", delete=False)  # noqa: SIM115 - closed in the finally
    total = 0
    try:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > st.max_upload_bytes:
                raise PolicyBlocked(
                    f"the upload is larger than {st.max_upload_bytes // (1024 * 1024)} MB (server.max_upload_mb)"
                )
            spool.write(chunk)
        spool.close()
        data = Path(spool.name).read_bytes()
        digest = sha256_hex(data)
        dest = upload_path(st, digest, name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.move(spool.name, dest)
            dest.chmod(0o640)
    finally:
        spool.close()
        Path(spool.name).unlink(missing_ok=True)

    analysis = None
    if suffix not in ARCHIVE_SUFFIXES:
        analysis = DocumentAnalyzer().analyze_bytes(data, name)
    summary = _summary(analysis) if analysis is not None else DocumentSummary()
    media = analysis.media_type if analysis is not None else "application/octet-stream"
    row = st.store.ensure_project(project)
    saved = st.store.add_document(row["id"], name, media, digest, len(data), None, summary.model_dump(mode="json"))
    return DocumentOut(
        ref=f"upload:{saved['document_id']}",
        id=saved["document_id"],
        version=saved["version"],
        new_version=saved["new_version"],
        project=row["name"],
        name=name,
        media_type=media,
        size=len(data),
        sha256=digest,
        summary=summary,
    )


@router.get(
    "/documents",
    response_model=list[DocumentOut],
    tags=["Documents"],
    summary="List uploaded documents",
    description="The documents of a project (all projects when none is given), with their newest version.",
)
def list_documents(request: Request, project: str | None = None) -> Any:
    st = state_of(request)
    projects = [st.store.get_project(project)] if project else st.store.list_projects()
    out = []
    for proj in projects:
        for doc in st.store.list_documents(proj["id"]):
            versions = sorted(doc.get("versions") or [], key=lambda v: v["version"])
            if not versions:
                continue
            last = versions[-1]
            out.append(
                DocumentOut(
                    ref=f"upload:{doc['id']}",
                    id=doc["id"],
                    version=last["version"],
                    new_version=False,
                    project=proj["name"],
                    name=doc["name"],
                    media_type=doc["media_type"],
                    size=last["size"],
                    sha256=last["sha256"],
                    summary=DocumentSummary.model_validate(last.get("parsed") or {}),
                )
            )
    return out


# ================================================================================================= discovery
@router.post(
    "/discover",
    response_model=DiscoverResponse,
    tags=["Discovery"],
    summary="Discover a target",
    description=(
        "Fingerprint a target: what kind of agent it is, its tools, interfaces, data sources, authentication model and risks. "
        "Only harmless probes are sent (none with `probe: false`). The target is registered and the profile stored with it."
    ),
)
async def discover(body: DiscoverRequest, request: Request) -> Any:
    st = state_of(request)
    if (body.target_id is None) == (body.target is None):
        raise UserError("give exactly one of `target_id` and `target`")
    if body.target_id:
        row, spec = st.store.get_target(body.target_id)
        target_id = row["id"]
        runnable = prepare_target(st, spec)
    else:
        assert body.target is not None
        spec = body.target
        runnable = prepare_target(st, spec)  # refuses a secret or a path it must not read, before anything is stored
        project = st.store.ensure_project(body.project)
        row = st.store.add_target(project["id"], spec)
        target_id = row["id"]
    st.services.credentials.reload()
    result = await st.services.discover(runnable, probe=body.probe)
    st.store.save_profile(target_id, None, result.profile)
    return DiscoverResponse(target_id=target_id, profile=result.profile, warnings=list(result.warnings))
