"""CredentialManager (spec sections 2E and 11).

* Secrets live in an encrypted store (Fernet / AES-128-CBC + HMAC) or in environment
  variables referenced as ``env:NAME``. Raw values are never written to config,
  logs, traces, reports or the database.
* Each :class:`CredentialProfile` has a scope (hosts it may be sent to), an optional
  expiry, a test-only flag and a rotation history (timestamps only).
* Every resolved value is registered with the :class:`SecretRedactor`.
"""

from __future__ import annotations

import base64
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from pydantic import Field

from agentlab.core.errors import CredentialError
from agentlab.core.ids import utcnow
from agentlab.core.models.base import Model
from agentlab.security.redactor import SecretRedactor, get_redactor

CredentialKind = Literal[
    "api_key", "bearer", "basic", "headers", "cookies", "oauth_token", "browser_state", "client_cert", "env",
]


class CredentialProfile(Model):
    """Metadata about a credential. Secret material is stored separately and encrypted."""

    name: str
    kind: CredentialKind
    description: str = ""
    scopes: list[str] = Field(default_factory=list, description="Hosts/URL prefixes this credential may reach")
    test_only: bool = True
    header_name: str | None = None
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    rotated_at: list[datetime] = Field(default_factory=list)
    secret_version: int = 1
    references: dict[str, str] = Field(
        default_factory=dict, description="field -> env:NAME reference (alternative to encrypted values)"
    )

    def public_view(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["fields"] = sorted(self.references)  # names only
        return data


class EncryptedSecretStore:
    """File-backed Fernet store. Key from ``AGENTLAB_MASTER_KEY`` or a 0600 key file."""

    def __init__(self, path: str | Path, key: bytes | None = None) -> None:
        self.path = Path(path)
        self._fernet = Fernet(key or self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        env = os.environ.get("AGENTLAB_MASTER_KEY")
        if env:
            return env.encode()
        key_path = self.path.with_suffix(".key")
        if key_path.exists():
            mode = key_path.stat().st_mode
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise CredentialError(f"master key file {key_path} must not be group/world accessible")
            return key_path.read_bytes().strip()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
        return key

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"profiles": {}, "secrets": {}}
        try:
            raw = self._fernet.decrypt(self.path.read_bytes())
        except InvalidToken as exc:
            raise CredentialError("secret store could not be decrypted (wrong master key?)") from exc
        return json.loads(raw)

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = self._fernet.encrypt(json.dumps(data, default=str).encode())
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(token)
        os.replace(tmp, self.path)

    def load(self) -> dict[str, Any]:
        return self._read()

    def save(self, data: dict[str, Any]) -> None:
        self._write(data)


class CredentialManager:
    def __init__(self, store: EncryptedSecretStore | None = None, redactor: SecretRedactor | None = None) -> None:
        self.store = store
        self.redactor = redactor or get_redactor()
        self._profiles: dict[str, CredentialProfile] = {}
        self._secrets: dict[str, dict[str, str]] = {}
        if store is not None:
            data = store.load()
            for name, p in data.get("profiles", {}).items():
                self._profiles[name] = CredentialProfile.model_validate(p)
            self._secrets = data.get("secrets", {})
            for fields in self._secrets.values():
                for v in fields.values():
                    self.redactor.register_secret(v)

    # ---- persistence -------------------------------------------------------
    def _persist(self) -> None:
        if self.store is None:
            return
        self.store.save({
            "profiles": {n: p.model_dump(mode="json") for n, p in self._profiles.items()},
            "secrets": self._secrets,
        })

    # ---- profile management -----------------------------------------------
    def add(self, profile: CredentialProfile, secrets: dict[str, str] | None = None) -> CredentialProfile:
        if profile.name in self._profiles:
            raise CredentialError(f"credential profile '{profile.name}' already exists; use rotate()")
        for field, ref in profile.references.items():
            if not ref.startswith("env:"):
                raise CredentialError(f"reference for '{field}' must look like env:NAME")
        self._profiles[profile.name] = profile
        if secrets:
            self._secrets[profile.name] = dict(secrets)
            for v in secrets.values():
                self.redactor.register_secret(v, label=f"credential:{profile.name}")
        self._persist()
        return profile

    def rotate(self, name: str, secrets: dict[str, str]) -> CredentialProfile:
        profile = self.get_profile(name)
        self._secrets[name] = dict(secrets)
        for v in secrets.values():
            self.redactor.register_secret(v, label=f"credential:{name}")
        profile.rotated_at.append(utcnow())
        profile.secret_version += 1
        self._persist()
        return profile

    def remove(self, name: str) -> None:
        self._profiles.pop(name, None)
        self._secrets.pop(name, None)
        self._persist()

    def get_profile(self, name: str) -> CredentialProfile:
        try:
            return self._profiles[name]
        except KeyError as exc:
            raise CredentialError(f"credential profile '{name}' is not configured") from exc

    def has(self, name: str) -> bool:
        return name in self._profiles

    def list_profiles(self) -> list[dict[str, Any]]:
        return [p.public_view() for p in self._profiles.values()]

    # ---- resolution --------------------------------------------------------
    def _check_valid(self, profile: CredentialProfile) -> None:
        if profile.expires_at and profile.expires_at < utcnow():
            raise CredentialError(f"credential profile '{profile.name}' expired at {profile.expires_at}")

    def _check_scope(self, profile: CredentialProfile, url: str | None) -> None:
        if not profile.scopes or url is None:
            return
        host = urlparse(url).hostname or ""
        for scope in profile.scopes:
            if "://" in scope and url.startswith(scope):
                return
            if host == scope or host.endswith("." + scope.lstrip("*.")):
                return
        raise CredentialError(
            f"credential profile '{profile.name}' is not scoped for host '{host}'"
        )

    def fields(self, name: str, url: str | None = None) -> dict[str, str]:
        profile = self.get_profile(name)
        self._check_valid(profile)
        self._check_scope(profile, url)
        out = dict(self._secrets.get(name, {}))
        for field, ref in profile.references.items():
            out[field] = resolve_reference(ref)
        for v in out.values():
            self.redactor.register_secret(v, label=f"credential:{name}")
        return out

    def auth_headers(self, name: str, url: str | None = None) -> dict[str, str]:
        profile = self.get_profile(name)
        f = self.fields(name, url)
        if profile.kind == "bearer" or profile.kind == "oauth_token":
            return {"Authorization": f"Bearer {f['token']}"}
        if profile.kind == "api_key":
            return {profile.header_name or "X-API-Key": f["key"]}
        if profile.kind == "basic":
            raw = f"{f['username']}:{f['password']}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        if profile.kind == "headers":
            return dict(f)
        if profile.kind == "cookies":
            return {"Cookie": "; ".join(f"{k}={v}" for k, v in f.items())}
        raise CredentialError(f"credential kind '{profile.kind}' cannot be expressed as HTTP headers")

    @contextmanager
    def browser_state_file(self, name: str, url: str | None = None):
        """Materialise a Playwright storage state to a private temp file, deleted afterwards."""
        profile = self.get_profile(name)
        if profile.kind != "browser_state":
            raise CredentialError(f"credential '{name}' is not a browser_state profile")
        state = self.fields(name, url)["state"]
        fd, path = tempfile.mkstemp(prefix="agentlab-state-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(state)
            os.chmod(path, 0o600)
            yield path
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def resolve_reference(ref: str) -> str:
    """Resolve ``env:NAME`` references. Raw values are rejected to keep secrets out of config."""
    if ref.startswith("env:"):
        name = ref[4:]
        value = os.environ.get(name)
        if not value:
            raise CredentialError(f"environment variable {name} referenced by a credential is not set")
        get_redactor().register_secret(value, label=f"env:{name}")
        return value
    raise CredentialError("secret references must use env:NAME or a credential profile (secret:<profile>)")
