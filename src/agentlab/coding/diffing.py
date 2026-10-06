"""Compare two copies of a project: which files a coding agent changed, and the unified diff of the change.

AgentLab never runs ``git`` on a tree an agent has touched (a repository the agent controls can carry hooks and
filters). It snapshots the plain files before the agent runs and again afterwards and compares them itself.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

SKIP_PARTS = frozenset({".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"})
MAX_FILES = 3000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_DIFF_CHARS = 200_000
MAX_CAPTURED_TEXT = 64 * 1024


def read_tree(root: Path, *, max_files: int = MAX_FILES, max_file_bytes: int = MAX_FILE_BYTES) -> dict[str, bytes]:
    """Every regular file below ``root`` (relative POSIX path -> content). Links, caches and VCS data are skipped.
    A file larger than ``max_file_bytes`` is represented by its size, so that a huge write still shows up as a change."""
    out: dict[str, bytes] = {}
    base = root.resolve()
    for path in sorted(base.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(base)
        if SKIP_PARTS.intersection(rel.parts) or rel.suffix == ".pyc":
            continue
        if len(out) >= max_files:
            break
        size = path.stat().st_size
        out[rel.as_posix()] = path.read_bytes() if size <= max_file_bytes else f"<{size} bytes>".encode()
    return out


def is_text(data: bytes) -> bool:
    if b"\x00" in data[:8000]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def decode(data: bytes) -> str:
    return data.decode("utf-8", "replace")


@dataclass
class TreeDiff:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    diff: str = ""
    diff_lines: int = 0  # added plus removed lines of the text files
    truncated: bool = False

    @property
    def changed_files(self) -> list[str]:
        return sorted({*self.added, *self.modified, *self.deleted})


def _lines(data: bytes) -> list[str]:
    return decode(data).splitlines()


def diff_trees(before: dict[str, bytes], after: dict[str, bytes]) -> TreeDiff:
    out = TreeDiff()
    chunks: list[str] = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        if old is None:
            out.added.append(path)
        elif new is None:
            out.deleted.append(path)
        else:
            out.modified.append(path)
        if (old is not None and not is_text(old)) or (new is not None and not is_text(new)):
            out.diff_lines += 1
            chunks.append(f"Binary file {path} {'added' if old is None else 'deleted' if new is None else 'changed'}\n")
            continue
        a = _lines(old) if old is not None else []
        b = _lines(new) if new is not None else []
        body = list(
            difflib.unified_diff(
                a,
                b,
                fromfile="/dev/null" if old is None else f"a/{path}",
                tofile="/dev/null" if new is None else f"b/{path}",
                lineterm="",
            )
        )
        out.diff_lines += sum(1 for line in body if line[:1] in "+-" and not line.startswith(("+++", "---")))
        chunks.append("\n".join(body) + "\n")
    text = "".join(chunks)
    if len(text) > MAX_DIFF_CHARS:
        out.truncated = True
        text = text[:MAX_DIFF_CHARS] + "\n...[diff truncated]\n"
    out.diff = text
    return out
