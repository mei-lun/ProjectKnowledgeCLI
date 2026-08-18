from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_knowledge.config import ProjectConfig
from project_knowledge.models import KnowledgeRecord
from project_knowledge.store import KnowledgeStore
from project_knowledge.vector import (
    DeterministicLocalProvider,
    EmbeddingProvider,
    ProviderUnavailable,
    VectorIndex,
)


def record(record_id: str, content: str) -> KnowledgeRecord:
    return KnowledgeRecord(
        id=record_id,
        kind="module",
        title=record_id,
        path=f".project-kb/generated/{record_id}.md",
        ownership="generated",
        confidence="generated",
        content=content,
    )


class BrokenProvider:
    provider_id = "broken"
    model_id = "broken-v1"
    dimension = 8

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ProviderUnavailable("provider unavailable")


class InvalidDimensionProvider:
    provider_id = "invalid"
    model_id = "invalid-v1"
    dimension = 8

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class FlatProvider:
    provider_id = "local"
    model_id = "flat-v1"
    dimension = 2

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class FakeService:
    def __init__(self, root: Path, config: ProjectConfig, db_path: Path) -> None:
        self.root = root
        self.config = config
        self.db_path = db_path

    def status(self) -> dict[str, object]:
        return {"pending_files": []}


class VectorRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db_path = self.root / ".project-kb" / "index.db"
        self.store = KnowledgeStore(self.db_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_disabled_does_not_load_provider_or_write_vectors(self) -> None:
        class ExplodingProvider:
            def __init__(self) -> None:
                raise AssertionError("disabled mode must not construct a provider")

        index = VectorIndex(
            self.store,
            ProjectConfig(embeddings="disabled"),
            provider_factory=ExplodingProvider,
        )

        result = index.sync([record("alpha", "alpha content")])

        self.assertFalse(result["enabled"])
        self.assertEqual(result["indexed"], 0)
        self.assertEqual(self.store.rows("SELECT * FROM vector_documents"), [])
        self.assertEqual(index.search("alpha", limit=3)[0], [])

    def test_local_provider_is_deterministic_and_dimension_stable(self) -> None:
        provider = DeterministicLocalProvider(dimension=16)

        first = provider.embed(["Alpha beta", "alpha beta"])
        second = provider.embed(["Alpha beta", "alpha beta"])

        self.assertEqual(first, second)
        self.assertEqual(provider.dimension, 16)
        self.assertTrue(all(len(vector) == 16 for vector in first))

    def test_unsupported_embedding_mode_remains_a_configuration_warning(self) -> None:
        fields = {item["field"] for item in ProjectConfig(embeddings="remote").capability_warnings()}

        self.assertIn("retrieval.embeddings", fields)

    def test_sync_rebuilds_on_content_provider_or_dimension_change_and_deletes(self) -> None:
        config = ProjectConfig(embeddings="local")
        first_provider = DeterministicLocalProvider(dimension=8, model_id="local-v1")
        index = VectorIndex(self.store, config, provider=first_provider)
        self.store.upsert_knowledge(record("alpha", "alpha content"))

        first = index.sync([record("alpha", "alpha content")])
        unchanged = index.sync([record("alpha", "alpha content")])
        changed = index.sync([record("alpha", "different content")])
        model_changed = VectorIndex(
            self.store,
            config,
            provider=DeterministicLocalProvider(dimension=8, model_id="local-v2"),
        ).sync([record("alpha", "different content")])
        dimension_changed = VectorIndex(
            self.store,
            config,
            provider=DeterministicLocalProvider(dimension=12, model_id="local-v2"),
        ).sync([record("alpha", "different content")])
        deleted = index.sync([])

        self.assertEqual(first["indexed"], 1)
        self.assertEqual(unchanged["indexed"], 0)
        self.assertEqual(changed["indexed"], 1)
        self.assertEqual(model_changed["indexed"], 1)
        self.assertEqual(dimension_changed["indexed"], 1)
        self.assertEqual(deleted["deleted"], 1)
        self.assertEqual(self.store.rows("SELECT * FROM vector_documents"), [])

    def test_provider_unavailable_is_explicit_fallback(self) -> None:
        index = VectorIndex(
            self.store,
            ProjectConfig(embeddings="local"),
            provider=BrokenProvider(),
        )

        sync = index.sync([record("alpha", "alpha content")])
        results, search = index.search("alpha", limit=3)

        self.assertEqual(results, [])
        self.assertTrue(sync["fallback"])
        self.assertTrue(search["fallback"])
        self.assertEqual(search["fallback_reason"], "provider_unavailable")

    def test_invalid_dimension_is_explicit_fallback(self) -> None:
        index = VectorIndex(
            self.store,
            ProjectConfig(embeddings="local"),
            provider=InvalidDimensionProvider(),
        )

        result = index.sync([record("alpha", "alpha content")])

        self.assertTrue(result["fallback"])
        self.assertEqual(result["fallback_reason"], "invalid_dimension")
        self.assertEqual(self.store.rows("SELECT * FROM vector_documents"), [])

    def test_hybrid_keeps_lexical_hit_ahead_of_pure_vector_candidate(self) -> None:
        from project_knowledge.retrieval import KnowledgeAPI

        config = ProjectConfig(embeddings="local")
        lexical = record("lexical", "exact lexical evidence")
        vector_only = record("vector-only", "unrelated evidence")
        self.store.upsert_knowledge(lexical)
        self.store.upsert_knowledge(vector_only)
        VectorIndex(self.store, config, provider=FlatProvider()).sync([lexical, vector_only])
        self.store.connection.commit()
        api = KnowledgeAPI.__new__(KnowledgeAPI)
        api.service = FakeService(self.root, config, self.db_path)
        api.root = self.root
        api.config = config

        with patch(
            "project_knowledge.retrieval.VectorIndex",
            lambda store, active_config: VectorIndex(store, active_config, provider=FlatProvider()),
        ):
            result = api.search("exact", limit=2)

        self.assertEqual([item["id"] for item in result["results"]], ["lexical", "vector-only"])
        self.assertTrue(result["vector_retrieval"]["enabled"])
        self.assertEqual(result["vector_retrieval"]["candidate_count"], 2)


if __name__ == "__main__":
    unittest.main()
