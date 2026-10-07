"""Vector storage adapters and a dependency-free embedder.

``HashingEmbedder`` is a *lexical* feature-hashing embedder (bag of words). It is not
semantic; it exists so retrieval-style evaluation, duplicate detection and the
test-suite work offline. Real semantic embeddings come from an ``LLMProvider.embed``.
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agentlab.core.errors import UnsupportedCapability, UserError
from agentlab.core.plugins import Registry

_WORD = re.compile(r"[A-Za-z0-9_]+")


class HashingEmbedder:
    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in _WORD.findall(text.lower()):
            h = int.from_bytes(hashlib.blake2b(w.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


@dataclass
class VectorHit:
    id: str
    score: float
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore(ABC):
    @abstractmethod
    def add(self, id: str, vector: list[float], text: str, metadata: dict[str, Any] | None = None) -> None: ...

    @abstractmethod
    def search(self, vector: list[float], k: int = 5) -> list[VectorHit]: ...

    @abstractmethod
    def __len__(self) -> int: ...


class InMemoryVectorStore(VectorStore):
    def __init__(self) -> None:
        self._rows: dict[str, tuple[list[float], str, dict[str, Any]]] = {}

    def add(self, id, vector, text, metadata=None):  # type: ignore[no-untyped-def]
        self._rows[id] = (vector, text, metadata or {})

    def search(self, vector, k=5):  # type: ignore[no-untyped-def]
        scored = [VectorHit(i, cosine(vector, v), t, m) for i, (v, t, m) in self._rows.items()]
        return sorted(scored, key=lambda h: -h.score)[:k]

    def __len__(self) -> int:
        return len(self._rows)


class PgVectorStore(VectorStore):
    """PostgreSQL + pgvector. Requires the ``vector`` extension and ``psycopg``."""

    def __init__(self, url: str, table: str = "agentlab_vectors", dim: int = 256) -> None:
        import psycopg  # noqa: PLC0415

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise ValueError("invalid table name")
        self.table, self.dim = table, dim
        self.conn = psycopg.connect(url, autocommit=True)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self.conn.execute(
            f"CREATE TABLE IF NOT EXISTS {table} (id text PRIMARY KEY, embedding vector({dim}), "  # noqa: S608
            "content text, metadata jsonb)"
        )

    def add(self, id, vector, text, metadata=None):  # type: ignore[no-untyped-def]
        import json

        self.conn.execute(
            f"INSERT INTO {self.table} (id, embedding, content, metadata) VALUES (%s, %s::vector, %s, %s) "  # noqa: S608
            "ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding, content = EXCLUDED.content, "
            "metadata = EXCLUDED.metadata",
            (id, "[" + ",".join(map(str, vector)) + "]", text, json.dumps(metadata or {})),
        )

    def search(self, vector, k=5):  # type: ignore[no-untyped-def]
        rows = self.conn.execute(
            f"SELECT id, 1 - (embedding <=> %s::vector) AS score, content, metadata FROM {self.table} "  # noqa: S608
            "ORDER BY embedding <=> %s::vector LIMIT %s",
            ("[" + ",".join(map(str, vector)) + "]",) * 2 + (k,),
        ).fetchall()
        return [VectorHit(r[0], float(r[1]), r[2], r[3] or {}) for r in rows]

    def __len__(self) -> int:
        row = self.conn.execute(f"SELECT count(*) FROM {self.table}").fetchone()  # noqa: S608
        return int(row[0]) if row else 0


class UnsupportedVectorStore(VectorStore):
    """A backend the adapter interface allows for and this build does not implement. Selecting it says so, in words."""

    backend = "unsupported"

    def __init__(self, *a: Any, **k: Any) -> None:
        raise UnsupportedCapability(
            f"vector store '{self.backend}' is not implemented in this build; implement VectorStore and register it "
            "with agentlab.storage.vectors.VECTOR_STORES (docs/plugins.md)"
        )

    def add(self, *a, **k):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def search(self, *a, **k):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def __len__(self) -> int:
        return 0


class QdrantVectorStore(UnsupportedVectorStore):
    backend = "qdrant"


VECTOR_STORES: Registry[type[VectorStore]] = Registry("vector_stores")
VECTOR_STORES.register("memory", InMemoryVectorStore, replace=True)
VECTOR_STORES.register("pgvector", PgVectorStore, replace=True)
VECTOR_STORES.register("qdrant", QdrantVectorStore, replace=True)


def create_vector_store(name: str, **options: Any) -> VectorStore:
    """The vector store ``name`` (built-in or plug-in), built with ``options`` (``url=`` for pgvector)."""
    try:
        cls = VECTOR_STORES.get(name)
    except KeyError:
        raise UserError(f"unknown vector store '{name}' (known: {', '.join(VECTOR_STORES.names())})") from None
    return cls(**options)
