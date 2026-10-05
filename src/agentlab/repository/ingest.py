"""Safe repository ingestion (spec sections 2A and 10).

Every imported repository is untrusted. Ingestion therefore *never executes repository code*:

* git clones run with hooks, templates, filters, submodules, fsmonitor, symlinks and every
  protocol except https disabled, a scrubbed environment (no global/system git config, no
  terminal prompts) and credentials passed through environment config (not argv)
* archives are extracted by AgentLab (no ``tar``/``unzip`` binaries) with zip-slip protection,
  link/device rejection and file-count / size / compression-ratio limits
* local directories are copied (never mounted) without following symlinks
* the ``.git`` directory is parsed as plain text for HEAD, never invoked through git
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from agentlab.core.errors import PolicyBlocked, SandboxError, UserError
from agentlab.core.models import RepositorySource
from agentlab.repository.languages import SKIP_DIRS
from agentlab.security.egress import EgressPolicy

MAX_FILES = 20_000
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_RATIO = 200  # uncompressed/compressed, per archive member
GIT_TIMEOUT = 180


@dataclass
class IngestedRepo:
    path: Path
    name: str
    commit: str | None = None
    ref: str | None = None
    url: str | None = None
    file_count: int = 0
    total_bytes: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    cleanup_root: Path | None = None

    def cleanup(self) -> None:
        if self.cleanup_root and self.cleanup_root.exists():
            shutil.rmtree(self.cleanup_root, ignore_errors=True)


def read_git_head(repo: Path) -> tuple[str | None, str | None]:
    """(commit, ref) by parsing .git as text - git itself is never run on untrusted repositories."""
    git = repo / ".git"
    if not git.is_dir():
        return None, None
    try:
        head = (git / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None, None
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        p = git / ref
        if p.is_file():
            return p.read_text(errors="replace").strip()[:40], ref
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(errors="replace").splitlines():
                if line.endswith(" " + ref):
                    return line.split()[0][:40], ref
        return None, ref
    return head[:40], None


def _safe_member(name: str) -> bool:
    p = Path(name)
    return not p.is_absolute() and ".." not in p.parts


def extract_archive(archive: Path, dest: Path) -> tuple[int, int, dict[str, int]]:
    """Extract zip / tar(.gz/.bz2/.xz) safely. Returns (files, bytes, skipped-reasons)."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    skipped: dict[str, int] = {}
    files = total = 0

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    def write(rel: str, data_iter, size: int, compress_size: int | None) -> None:  # type: ignore[no-untyped-def]
        nonlocal files, total
        target = (root / rel).resolve()
        if root not in target.parents:
            skip("path traversal")
            raise PolicyBlocked(f"archive member '{rel}' escapes the extraction directory")
        if size > MAX_FILE_BYTES:
            skip("file too large")
            return
        if compress_size and compress_size > 0 and size / compress_size > MAX_RATIO and size > 1_000_000:
            raise PolicyBlocked(f"archive member '{rel}' has a suspicious compression ratio (zip bomb)")
        files += 1
        total += size
        if files > MAX_FILES or total > MAX_TOTAL_BYTES:
            raise PolicyBlocked("archive exceeds extraction limits (file count / total size)")
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with target.open("wb") as out:
            for chunk in data_iter:
                written += len(chunk)
                if written > MAX_FILE_BYTES:
                    raise PolicyBlocked(f"archive member '{rel}' is larger than declared")
                out.write(chunk)
        os.chmod(target, 0o644)

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not _safe_member(info.filename):
                    skip("path traversal")
                    raise PolicyBlocked(f"archive member '{info.filename}' has an unsafe path")
                mode = (info.external_attr >> 16) & 0xFFFF
                if mode and stat.S_ISLNK(mode):
                    skip("symlink")
                    continue
                with zf.open(info) as fh:
                    write(info.filename, iter(lambda fh=fh: fh.read(65536), b""), info.file_size, info.compress_size)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive, "r:*") as tf:
            for m in tf:
                if m.isdir():
                    continue
                if not m.isfile():  # symlinks, hardlinks, devices, fifos
                    skip("link/device")
                    continue
                if not _safe_member(m.name):
                    skip("path traversal")
                    raise PolicyBlocked(f"archive member '{m.name}' has an unsafe path")
                fh = tf.extractfile(m)
                if fh is None:
                    continue
                write(m.name, iter(lambda fh=fh: fh.read(65536), b""), m.size, None)
    else:
        raise UserError(f"{archive.name} is not a supported archive (zip, tar, tar.gz, tar.bz2, tar.xz)")
    return files, total, skipped


def copy_tree(src: Path, dest: Path) -> tuple[int, int, dict[str, int]]:
    """Copy a directory without following symlinks, skipping build/vendor directories."""
    src = src.resolve()
    skipped: dict[str, int] = {}
    files = total = 0
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        keep = []
        for d in dirnames:
            full = Path(dirpath) / d
            if d in SKIP_DIRS and d != ".git":
                skipped["vendor/build directory"] = skipped.get("vendor/build directory", 0) + 1
            elif full.is_symlink():
                skipped["symlink"] = skipped.get("symlink", 0) + 1
            else:
                keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            p = Path(dirpath) / f
            if p.is_symlink() or not p.is_file():
                skipped["symlink"] = skipped.get("symlink", 0) + 1
                continue
            size = p.stat().st_size
            rel = p.relative_to(src)
            if ".git" in rel.parts and rel.parts[0] == ".git" and rel.as_posix() not in {".git/HEAD", ".git/packed-refs"} \
                    and not rel.as_posix().startswith(".git/refs/"):
                continue  # keep only what is needed to read the commit id
            if size > MAX_FILE_BYTES:
                skipped["file too large"] = skipped.get("file too large", 0) + 1
                continue
            files += 1
            total += size
            if files > MAX_FILES or total > MAX_TOTAL_BYTES:
                raise PolicyBlocked("repository exceeds ingestion limits (file count / total size)")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
            os.chmod(target, 0o644)
    return files, total, skipped


async def _git_clone(url: str, ref: str | None, dest: Path, token_header: str | None) -> None:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(dest.parent), "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true",
        "GIT_LFS_SKIP_SMUDGE": "1", "GIT_ALLOW_PROTOCOL": "https", "LC_ALL": "C",
    }
    for k in ("HTTPS_PROXY", "https_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "GIT_SSL_CAINFO", "NO_PROXY", "no_proxy"):
        if k in os.environ:
            env[k] = os.environ[k]
    n = 0
    if token_header:  # passed through env config so it never appears in argv / process listings
        env[f"GIT_CONFIG_KEY_{n}"] = "http.extraHeader"
        env[f"GIT_CONFIG_VALUE_{n}"] = token_header
        n += 1
    if n:
        env["GIT_CONFIG_COUNT"] = str(n)
    args = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.symlinks=false", "-c", "core.fsmonitor=false",
            "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "filter.lfs.smudge=",
            "-c", "filter.lfs.process=", "-c", "filter.lfs.required=false", "-c", "submodule.recurse=false",
            "clone", "--depth", "1", "--no-tags", "--single-branch", "--no-recurse-submodules", "--template=",
            *(["--branch", ref] if ref else []), "--", url, str(dest)]
    proc = await asyncio.create_subprocess_exec(*args, env=env, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=GIT_TIMEOUT)
    except TimeoutError as exc:
        proc.kill()
        raise SandboxError(f"git clone timed out after {GIT_TIMEOUT}s") from exc
    if proc.returncode != 0:
        from agentlab.security.redactor import redact_text

        raise UserError(f"git clone failed: {redact_text(err.decode('utf-8', 'replace'))[:300]}")


class RepositoryIngestor:
    def __init__(self, workspace: Path | None = None, egress: EgressPolicy | None = None) -> None:
        self.workspace = workspace
        self.egress = egress or EgressPolicy()

    async def ingest(self, source: RepositorySource, *, auth_header: str | None = None) -> IngestedRepo:
        root = Path(tempfile.mkdtemp(prefix="agentlab-repo-", dir=self.workspace))
        os.chmod(root, 0o700)
        dest = root / "src"
        dest.mkdir()
        repo = IngestedRepo(path=dest, name="repository", ref=source.ref, url=source.url, cleanup_root=root)
        try:
            if source.url:
                self.egress.check(source.url)
                repo.name = source.url.rstrip("/").split("/")[-1].removesuffix(".git") or "repository"
                if source.url.startswith("git@"):
                    raise UserError("ssh repository URLs are not supported; use https:// (credentials via a credential profile)")
                shutil.rmtree(dest)
                await _git_clone(source.url, source.ref, dest, auth_header)
                commit, ref = read_git_head(dest)
                repo.commit, repo.ref = commit, source.ref or ref
                shutil.rmtree(dest / ".git", ignore_errors=True)
                repo.file_count, repo.total_bytes, repo.skipped = self._measure(dest)
            elif source.archive:
                a = Path(source.archive)
                if not a.is_file():
                    raise UserError(f"archive not found: {a}")
                repo.name = a.name.split(".")[0]
                repo.file_count, repo.total_bytes, repo.skipped = extract_archive(a, dest)
                self._unwrap_single_dir(repo)
            elif source.path:
                p = Path(source.path).expanduser()
                if not p.is_dir():
                    raise UserError(f"repository path is not a directory: {p}")
                repo.name = p.resolve().name
                repo.commit, repo.ref = read_git_head(p)
                repo.file_count, repo.total_bytes, repo.skipped = copy_tree(p, dest)
                shutil.rmtree(dest / ".git", ignore_errors=True)
            else:
                raise UserError("repository source needs one of: url, path, archive")
        except BaseException:
            repo.cleanup()
            raise
        if repo.skipped:
            repo.warnings.append("skipped during ingestion: " + ", ".join(f"{k} x{v}" for k, v in repo.skipped.items()))
        return repo

    @staticmethod
    def _measure(path: Path) -> tuple[int, int, dict[str, int]]:
        n = total = 0
        skipped: dict[str, int] = {}
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
            for f in filenames:
                p = Path(dirpath) / f
                if p.is_symlink():
                    skipped["symlink"] = skipped.get("symlink", 0) + 1
                    p.unlink()
                    continue
                n += 1
                total += p.stat().st_size
        if n > MAX_FILES or total > MAX_TOTAL_BYTES:
            raise PolicyBlocked("repository exceeds ingestion limits (file count / total size)")
        return n, total, skipped

    @staticmethod
    def _unwrap_single_dir(repo: IngestedRepo) -> None:
        entries = [e for e in repo.path.iterdir()]
        if len(entries) == 1 and entries[0].is_dir():
            inner = entries[0]
            for item in list(inner.iterdir()):
                shutil.move(str(item), repo.path / item.name)
            inner.rmdir()
