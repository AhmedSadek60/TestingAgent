"""File extension -> language mapping (language-agnostic by design: unknown extensions are 'other')."""

from __future__ import annotations

from pathlib import PurePosixPath

EXT_LANG: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".ipynb": "Python (notebook)",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".mts": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".scala": "Scala",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".php": "PHP",
    ".rb": "Ruby",
    ".swift": "Swift",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".m": "Objective-C",
    ".dart": "Dart",
    ".lua": "Lua",
    ".r": "R",
    ".jl": "Julia",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hs": "Haskell",
    ".clj": "Clojure",
    ".sh": "Shell",
    ".bash": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".html": "HTML",
    ".css": "CSS",
    ".vue": "Vue",
    ".svelte": "Svelte",
}
DOC_EXT = {".md", ".rst", ".txt", ".adoc"}
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".xml", ".gradle", ".properties"}
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "target",
    "dist",
    "build",
    ".next",
    ".idea",
    ".vscode",
    "vendor",
    "Pods",
    ".gradle",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "coverage",
    ".cache",
    ".terraform",
    "bin",
    "obj",
}
BINARY_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".jar",
    ".class",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".woff",
    ".woff2",
    ".ttf",
    ".mp4",
    ".mp3",
    ".wav",
    ".pyc",
    ".lock",
    ".sqlite",
    ".db",
}


def language_of(path: str) -> str | None:
    return EXT_LANG.get(PurePosixPath(path).suffix.lower())


def is_text_candidate(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() not in BINARY_EXT
