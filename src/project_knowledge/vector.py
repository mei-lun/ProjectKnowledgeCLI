from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from .config import ProjectConfig
from .models import KnowledgeRecord
from .store import KnowledgeStore


class ProviderUnavailable(RuntimeError):
    """Raised when an embedding provider cannot produce vectors."""


class EmbeddingProvider(Protocol):
    provider_id: str
    model_id: str
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    record_id: str
    similarity: float


class DeterministicLocalProvider:
    """Small, offline, deterministic provider used for local retrieval contracts."""

    provider_id = "local"

    def __init__(self, dimension: int = 64, model_id: str = "local-v1") -> None:
        if dimension <= 0:
            raise ValueError("embedding dimension must be positive")
        self.dimension = dimension
        self.model_id = model_id

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = re.findall(r"\w+", text.casefold(), flags=re.UNICODE)
        for token in tokens:
            digest = hashlib.sha256(f"{self.model_id}\0{token}".encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [round(value / norm, 8) for value in vector]
        return vector


class VectorIndex:
    def __init__(
        self,
        store: KnowledgeStore,
        config: ProjectConfig,
        *,
        provider: EmbeddingProvider | None = None,
        provider_factory: Callable[[], EmbeddingProvider] | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self._provider = provider
        self._provider_factory = provider_factory
        self._diagnostics = self._base_diagnostics()

    def _base_diagnostics(self) -> dict[str, object]:
        return {
            "enabled": self.config.embeddings == "local",
            "provider": "local" if self.config.embeddings == "local" else "disabled",
            "model": self.config.provider_model or ("local-v1" if self.config.embeddings == "local" else ""),
            "indexed": 0,
            "candidate_count": 0,
            "fallback": False,
            "fallback_reason": None,
            "duration_ms": 0,
        }

    def _provider_or_raise(self) -> EmbeddingProvider:
        if self.config.embeddings != "local":
            raise ProviderUnavailable("embeddings_disabled" if self.config.embeddings == "disabled" else "unsupported_embeddings")
        if self._provider is None:
            factory = self._provider_factory or (
                lambda: DeterministicLocalProvider(model_id=self.config.provider_model or "local-v1")
            )
            try:
                self._provider = factory()
            except ProviderUnavailable:
                raise
            except Exception as error:
                raise ProviderUnavailable(str(error)) from error
        return self._provider

    @staticmethod
    def _record_text(record: KnowledgeRecord) -> str:
        return "\n".join([record.title, " ".join(record.tags), record.content])

    @classmethod
    def _content_hash(cls, record: KnowledgeRecord) -> str:
        return "sha256:" + hashlib.sha256(cls._record_text(record).encode("utf-8")).hexdigest()

    def sync(self, records: Sequence[KnowledgeRecord]) -> dict[str, object]:
        started = time.monotonic()
        result = self._base_diagnostics()
        if self.config.embeddings == "disabled":
            self._diagnostics = result
            return result
        try:
            provider = self._provider_or_raise()
            vectors = provider.embed([self._record_text(record) for record in records])
            self._validate_vectors(vectors, len(records), provider.dimension)
        except ProviderUnavailable as error:
            result.update({"fallback": True, "fallback_reason": self._reason(error)})
            result["duration_ms"] = int((time.monotonic() - started) * 1000)
            self._diagnostics = result
            return result
        except (TypeError, ValueError):
            result.update({"fallback": True, "fallback_reason": "invalid_dimension"})
            result["duration_ms"] = int((time.monotonic() - started) * 1000)
            self._diagnostics = result
            return result

        existing = {
            row["id"]: row
            for row in self.store.connection.execute("SELECT * FROM vector_documents")
        }
        record_ids = {record.id for record in records}
        indexed = 0
        for record, vector in zip(records, vectors):
            content_hash = self._content_hash(record)
            row = existing.get(record.id)
            if row and (
                row["content_hash"] == content_hash
                and row["provider_id"] == provider.provider_id
                and row["model_id"] == provider.model_id
                and int(row["dimension"]) == provider.dimension
            ):
                continue
            self.store.connection.execute(
                """
                INSERT INTO vector_documents
                    (id, content_hash, provider_id, model_id, dimension, vector_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    provider_id=excluded.provider_id,
                    model_id=excluded.model_id,
                    dimension=excluded.dimension,
                    vector_json=excluded.vector_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record.id,
                    content_hash,
                    provider.provider_id,
                    provider.model_id,
                    provider.dimension,
                    json.dumps(vector, separators=(",", ":")),
                    time.time(),
                ),
            )
            indexed += 1
        deleted = 0
        for record_id in set(existing) - record_ids:
            self.store.connection.execute("DELETE FROM vector_documents WHERE id = ?", (record_id,))
            deleted += 1
        result.update({
            "provider": provider.provider_id,
            "model": provider.model_id,
            "enabled": True,
            "indexed": indexed,
            "deleted": deleted,
            "fallback": False,
            "duration_ms": int((time.monotonic() - started) * 1000),
        })
        self._diagnostics = result
        return result

    def search(self, query: str, limit: int = 10) -> tuple[list[VectorSearchResult], dict[str, object]]:
        started = time.monotonic()
        result = self._base_diagnostics()
        if self.config.embeddings == "disabled":
            self._diagnostics = result
            return [], result
        try:
            provider = self._provider_or_raise()
            vectors = provider.embed([query])
            self._validate_vectors(vectors, 1, provider.dimension)
            query_vector = vectors[0]
        except ProviderUnavailable as error:
            result.update({"fallback": True, "fallback_reason": self._reason(error)})
            result["duration_ms"] = int((time.monotonic() - started) * 1000)
            self._diagnostics = result
            return [], result
        except (TypeError, ValueError):
            result.update({"fallback": True, "fallback_reason": "invalid_dimension"})
            result["duration_ms"] = int((time.monotonic() - started) * 1000)
            self._diagnostics = result
            return [], result

        matches: list[VectorSearchResult] = []
        corrupt = 0
        for row in self.store.connection.execute("SELECT id, dimension, vector_json FROM vector_documents"):
            try:
                vector = json.loads(row["vector_json"])
                if int(row["dimension"]) != provider.dimension or len(vector) != provider.dimension:
                    corrupt += 1
                    continue
                similarity = sum(left * right for left, right in zip(query_vector, vector))
                if similarity > 0:
                    matches.append(VectorSearchResult(row["id"], round(float(similarity), 8)))
            except (TypeError, ValueError, json.JSONDecodeError):
                corrupt += 1
        matches.sort(key=lambda item: (-item.similarity, item.record_id))
        result.update({
            "provider": provider.provider_id,
            "model": provider.model_id,
            "enabled": True,
            "candidate_count": len(matches),
            "corrupt_count": corrupt,
            "duration_ms": int((time.monotonic() - started) * 1000),
        })
        self._diagnostics = result
        return matches[: max(1, min(limit, 100))], result

    def diagnostics(self) -> dict[str, object]:
        return dict(self._diagnostics)

    @staticmethod
    def _validate_vectors(vectors: object, expected: int, dimension: int) -> None:
        if not isinstance(vectors, list) or len(vectors) != expected:
            raise ValueError("provider returned an invalid vector count")
        if any(not isinstance(vector, list) or len(vector) != dimension for vector in vectors):
            raise ValueError("provider returned an invalid vector dimension")
        if any(not isinstance(value, (int, float)) for vector in vectors for value in vector):
            raise ValueError("provider returned non-numeric vector values")

    @staticmethod
    def _reason(error: ProviderUnavailable) -> str:
        value = str(error).strip()
        if value in {"embeddings_disabled", "unsupported_embeddings", "provider unavailable"}:
            return "provider_unavailable" if value == "provider unavailable" else value
        return "provider_unavailable"
