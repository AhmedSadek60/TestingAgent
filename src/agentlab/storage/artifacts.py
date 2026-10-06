"""Artifact storage (spec section 26).

Artifacts are content-addressed (``sha256-<hex>``) so identical evidence is stored once and
references are stable and verifiable. Sensitive artifacts (browser state, screenshots taken
while authenticated, raw traces) are kept in a restricted area with 0700/0600 permissions and
are excluded from shareable report bundles unless explicitly requested.

Text-like artifacts are passed through the SecretRedactor before they are written.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import Field

from agentlab.core.errors import InfrastructureError
from agentlab.core.ids import utcnow
from agentlab.core.models.base import Model
from agentlab.core.plugins import Registry
from agentlab.security.redactor import get_redactor

_ID_RE = re.compile(r"^sha256-[0-9a-f]{64}$")
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/x-ndjson", "application/yaml")


class ArtifactRef(Model):
    id: str
    sha256: str
    kind: str
    media_type: str
    size: int
    sensitivity: str = "normal"  # normal | restricted
    name: str | None = None
    run_id: str | None = None
    test_key: str | None = None
    redacted: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())


def is_text_media(media_type: str) -> bool:
    return media_type.startswith(TEXT_TYPES)


class ArtifactStore(ABC):
    @abstractmethod
    def put(
        self,
        data: bytes | str | Path,
        *,
        kind: str,
        media_type: str = "application/octet-stream",
        name: str | None = None,
        run_id: str | None = None,
        test_key: str | None = None,
        sensitivity: str = "normal",
        redact: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef: ...

    @abstractmethod
    def get(self, artifact_id: str) -> bytes: ...

    @abstractmethod
    def ref(self, artifact_id: str) -> ArtifactRef: ...

    @abstractmethod
    def list(self, run_id: str | None = None) -> list[ArtifactRef]: ...

    def get_json(self, artifact_id: str) -> Any:
        return json.loads(self.get(artifact_id))

    def put_json(self, obj: Any, *, kind: str, **kw: Any) -> ArtifactRef:
        kw.setdefault("media_type", "application/json")
        return self.put(json.dumps(obj, indent=2, sort_keys=True, default=str), kind=kind, **kw)

    def local_path(self, artifact_id: str) -> Path:
        raise InfrastructureError("this artifact store has no local filesystem path")


def _prepare(data: bytes | str | Path, media_type: str, redact: bool) -> tuple[bytes, bool]:
    if isinstance(data, Path):
        data = data.read_bytes()
    if isinstance(data, str):
        data = data.encode("utf-8")
    redacted = False
    if redact and is_text_media(media_type):
        text = data.decode("utf-8", errors="replace")
        clean, hits = get_redactor().redact_text(text)
        if hits:
            data, redacted = clean.encode("utf-8"), True
    return data, redacted


class LocalArtifactStore(ArtifactStore):
    """Filesystem store. Layout: ``objects/ab/<hash>`` and ``restricted/ab/<hash>`` plus ``meta/<hash>.json``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        for sub in ("objects", "restricted", "meta"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        os.chmod(self.root / "restricted", 0o700)

    @staticmethod
    def _check(artifact_id: str) -> str:
        if not _ID_RE.match(artifact_id):
            raise InfrastructureError("invalid artifact id")
        return artifact_id.removeprefix("sha256-")

    def _path(self, digest: str, sensitivity: str) -> Path:
        area = "restricted" if sensitivity == "restricted" else "objects"
        return self.root / area / digest[:2] / digest

    def put(
        self,
        data,
        *,
        kind,
        media_type="application/octet-stream",
        name=None,
        run_id=None,  # type: ignore[no-untyped-def]
        test_key=None,
        sensitivity="normal",
        redact=True,
        meta=None,
    ) -> ArtifactRef:
        raw, redacted = _prepare(data, media_type, redact)
        digest = hashlib.sha256(raw).hexdigest()
        path = self._path(digest, sensitivity)
        path.parent.mkdir(parents=True, exist_ok=True)
        if sensitivity == "restricted":
            os.chmod(path.parent, 0o700)
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if sensitivity == "restricted" else 0o640)
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
            os.replace(tmp, path)
        ref = ArtifactRef(
            id=f"sha256-{digest}",
            sha256=digest,
            kind=kind,
            media_type=media_type,
            size=len(raw),
            sensitivity=sensitivity,
            name=name,
            run_id=run_id,
            test_key=test_key,
            redacted=redacted,
            meta=meta or {},
        )
        # one metadata record per (content, run, test, name) so repeated evidence keeps its provenance
        key = hashlib.sha256(f"{digest}|{run_id}|{test_key}|{name}|{kind}".encode()).hexdigest()[:16]
        (self.root / "meta" / f"{digest}.{key}.json").write_text(ref.model_dump_json(indent=2), encoding="utf-8")
        return ref

    def ref(self, artifact_id: str) -> ArtifactRef:
        digest = self._check(artifact_id)
        metas = sorted((self.root / "meta").glob(f"{digest}.*.json"))
        if not metas:
            raise InfrastructureError(f"artifact {artifact_id} not found")
        return ArtifactRef.model_validate_json(metas[0].read_text(encoding="utf-8"))

    def local_path(self, artifact_id: str) -> Path:
        digest = self._check(artifact_id)
        ref = self.ref(artifact_id)
        p = self._path(digest, ref.sensitivity)
        if not p.exists():
            raise InfrastructureError(f"artifact {artifact_id} content missing")
        return p

    def get(self, artifact_id: str) -> bytes:
        return self.local_path(artifact_id).read_bytes()

    def list(self, run_id: str | None = None) -> list[ArtifactRef]:
        out = []
        for m in sorted((self.root / "meta").glob("*.json")):
            ref = ArtifactRef.model_validate_json(m.read_text(encoding="utf-8"))
            if run_id is None or ref.run_id == run_id:
                out.append(ref)
        return out

    def export(self, artifact_id: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.local_path(artifact_id), dest)
        return dest


class MemoryArtifactStore(ArtifactStore):
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._refs: list[ArtifactRef] = []

    def put(
        self,
        data,
        *,
        kind,
        media_type="application/octet-stream",
        name=None,
        run_id=None,  # type: ignore[no-untyped-def]
        test_key=None,
        sensitivity="normal",
        redact=True,
        meta=None,
    ) -> ArtifactRef:
        raw, redacted = _prepare(data, media_type, redact)
        digest = hashlib.sha256(raw).hexdigest()
        self._data[digest] = raw
        ref = ArtifactRef(
            id=f"sha256-{digest}",
            sha256=digest,
            kind=kind,
            media_type=media_type,
            size=len(raw),
            sensitivity=sensitivity,
            name=name,
            run_id=run_id,
            test_key=test_key,
            redacted=redacted,
            meta=meta or {},
        )
        self._refs.append(ref)
        return ref

    def get(self, artifact_id: str) -> bytes:
        try:
            return self._data[artifact_id.removeprefix("sha256-")]
        except KeyError as exc:
            raise InfrastructureError(f"artifact {artifact_id} not found") from exc

    def ref(self, artifact_id: str) -> ArtifactRef:
        for r in self._refs:
            if r.id == artifact_id:
                return r
        raise InfrastructureError(f"artifact {artifact_id} not found")

    def list(self, run_id: str | None = None) -> list[ArtifactRef]:
        return [r for r in self._refs if run_id is None or r.run_id == run_id]


class UnsupportedObjectStore(ArtifactStore):
    """S3/GCS-style stores are an extension point; no implementation ships in this build."""

    def __init__(self, *a: Any, **k: Any) -> None:
        raise InfrastructureError(
            "object-store artifact backends are not implemented in this build; "
            "implement ArtifactStore and register it in ARTIFACT_STORES"
        )

    def put(self, *a, **k):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def get(self, *a):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def ref(self, *a):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def list(self, *a, **k):  # type: ignore[no-untyped-def]
        raise NotImplementedError


ARTIFACT_STORES: Registry[type[ArtifactStore]] = Registry("artifact_stores")
ARTIFACT_STORES.register("local", LocalArtifactStore, replace=True)
ARTIFACT_STORES.register("memory", MemoryArtifactStore, replace=True)
ARTIFACT_STORES.register("s3", UnsupportedObjectStore, replace=True)
