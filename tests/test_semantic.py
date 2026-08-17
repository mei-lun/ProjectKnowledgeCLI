from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
import io
from pathlib import Path

from project_knowledge.evidence import EvidencePackBuilder
from project_knowledge.cli import main
from project_knowledge.provider import FakeProvider, ModelRuntime, ProviderConfig
from project_knowledge.proposal import ProposalService
from project_knowledge.retrieval import KnowledgeAPI
from project_knowledge.schemas import FEATURE_GUIDE_DRAFT_SCHEMA, SchemaValidationError, validate_instance
from project_knowledge.semantic import FeatureGuideValidationError, SemanticKnowledgeService
from project_knowledge.service import ProjectService
from project_knowledge.store import KnowledgeStore


BAG_SOURCE = '''
class BagService:
    def use_item(self, player_id: int, item_id: int) -> bool:
        return self._consume(player_id, item_id)

    def _consume(self, player_id: int, item_id: int) -> bool:
        return True
'''


class SemanticKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src" / "bag.py").write_text(BAG_SOURCE, encoding="utf-8")
        (self.root / "tests" / "test_bag.py").write_text(
            "from src.bag import BagService\n\ndef test_use_item():\n    assert BagService().use_item(1, 2)\n",
            encoding="utf-8",
        )
        (self.root / "pyproject.toml").write_text("[project]\nname='bag-sample'\n", encoding="utf-8")
        self.project = ProjectService(self.root)
        self.project.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _pack(self):
        return EvidencePackBuilder(self.root).build(
            "开发背包物品使用功能", ["src/bag.py", "tests/test_bag.py"], source_commit="fixture",
        )

    def _response(self, pack, *, bad_symbol: bool = False, document_authority: str | None = None):
        hashes = {item.path: item.content_hash for item in pack.items}
        live_symbol = self.project.engine.search_symbols(
            self.root, self.project.config, "use_item", limit=10
        )[0]
        symbol = {
            # Feature Guide citations use the public CodeGraph symbol name;
            # opaque adapter IDs are not persisted into user-authored evidence.
            "id": live_symbol.name,
            "path": live_symbol.path,
            "hash": hashes[live_symbol.path],
        }
        if bad_symbol:
            symbol = {"id": "missing::use_item", "path": "src/bag.py", "hash": symbol["hash"]}

        file_source = {
            "type": "file", "path": "src/bag.py", "line": 2,
            "hash": hashes["src/bag.py"], "authority": "source",
        }
        test_source = {
            "type": "file", "path": "tests/test_bag.py", "line": 1,
            "hash": hashes["tests/test_bag.py"], "authority": "source",
        }
        symbol_source = {
            "type": "symbol", "id": symbol["id"], "path": symbol["path"],
            "line": 3, "hash": symbol["hash"], "authority": "source",
        }
        if document_authority is not None:
            file_source = {
                "type": "file", "path": "docs/legacy.md", "line": 1,
                "hash": hashes["docs/legacy.md"], "authority": document_authority,
            }

        def statement(text: str, *sources):
            return {"text": text, "sources": list(sources or [file_source])}

        return {
            "schema_version": 1,
            "feature_id": "bag-item-use",
            "title": "背包物品使用",
            "domain": "背包",
            "lifecycle": "draft",
            "summary": statement("玩家通过背包服务使用物品。", symbol_source),
            "responsibilities": [statement("校验并消费指定物品。", symbol_source)],
            "entrypoints": [statement("BagService.use_item 是功能入口。", symbol_source)],
            "workflow": {
                "title": "物品使用流程",
                "steps": [
                    {"order": 1, **statement("调用 use_item 进入流程。", symbol_source)},
                    {"order": 2, **statement("调用 _consume 完成消费。", file_source)},
                ],
            },
            "dependencies": [statement("功能依赖 BagService。", symbol_source)],
            "data_and_state": [statement("输入包含玩家与物品标识。", symbol_source)],
            "invariants": [statement("消费结果以布尔值返回。", symbol_source)],
            "extension_points": [statement("在 use_item 中增加新的使用规则。", symbol_source)],
            "recipe": {
                "title": "扩展物品使用规则",
                "goal": "安全扩展背包物品使用行为",
                "prerequisites": [statement("先确认 use_item 的调用约定。", symbol_source)],
                "steps": [statement("修改 BagService.use_item。", symbol_source)],
                "verification": [statement("运行背包测试。", test_source)],
                "rollback": [statement("回退 bag.py 的规则修改。", file_source)],
            },
            "tests": [statement("test_use_item 覆盖成功路径。", test_source)],
            "pitfalls": [statement("修改返回类型会影响现有测试。", test_source)],
            "unknowns": [{
                "text": "物品配置是否还有运行时限制？",
                "reason": "当前证据包没有物品配置文件。",
                "needed_evidence": ["物品配置与运行时校验代码"],
            }],
        }

    def _runtime(self, response, *, cache: bool = False):
        config = ProviderConfig(
            provider_id="fake", model_id="fake-feature-guide", enabled=True,
            cache_enabled=cache, checkpoint_enabled=False,
            output_schema_version="feature-guide-draft-v1",
        )
        return ModelRuntime(self.root, FakeProvider(config, response), config)

    def test_feature_guide_schema_requires_sources_and_reserves_verified_for_review(self) -> None:
        pack = self._pack()
        response = self._response(pack)
        validate_instance(response, FEATURE_GUIDE_DRAFT_SCHEMA)

        response["summary"]["sources"] = []
        with self.assertRaises(SchemaValidationError):
            validate_instance(response, FEATURE_GUIDE_DRAFT_SCHEMA)

        response = self._response(pack)
        response["lifecycle"] = "verified"
        with self.assertRaises(SchemaValidationError):
            validate_instance(response, FEATURE_GUIDE_DRAFT_SCHEMA)

    def test_fake_provider_generates_persists_and_prioritizes_complete_chinese_guide(self) -> None:
        pack = self._pack()
        semantic = SemanticKnowledgeService(self.root, self._runtime(self._response(pack)))
        result = semantic.generate_feature_guide(pack, persist=True)

        self.assertEqual(result["record_id"], "draft.feature.bag-item-use")
        self.assertEqual(result["lifecycle"], "draft")
        markdown = self.root / ".project-kb" / "drafts" / "features" / "bag-item-use.md"
        structured = self.root / ".project-kb" / "drafts" / "features" / "bag-item-use.json"
        self.assertTrue(markdown.exists())
        self.assertTrue(structured.exists())
        content = markdown.read_text(encoding="utf-8")
        self.assertIn("# 功能指南：背包物品使用", content)
        self.assertIn("## 开发步骤", content)
        self.assertIn('project-kb:source symbol="', content)
        self.assertEqual(json.loads(structured.read_text(encoding="utf-8"))["title"], "背包物品使用")

        api = KnowledgeAPI(self.root)
        record = api.get("draft.feature.bag-item-use")
        self.assertEqual(record["kind"], "feature-guide")
        self.assertEqual(record["ownership"], "draft")
        self.assertEqual(record["confidence"], "generated")
        self.assertEqual(api.search("背包物品使用")["results"][0]["id"], "draft.feature.bag-item-use")
        context = api.context("开发背包物品使用功能", max_tokens=1600)
        self.assertEqual(context["knowledge"][0]["id"], "draft.feature.bag-item-use")
        self.assertTrue(context["knowledge"][0]["requires_live_source"])

        proposal_service = ProposalService(self.root)
        proposal = proposal_service.create_from_feature_draft("bag-item-use", change_range="HEAD")
        curated = self.root / ".project-kb" / "curated" / "features" / "bag-item-use.md"
        self.assertEqual(proposal.status, "pending")
        self.assertFalse(curated.exists())
        proposal_service.apply(proposal.proposal_id, reviewer="reviewer", review_reason="来源与内容已人工确认")
        self.assertIn('project-kb:generated id="feature-bag-item-use"', curated.read_text(encoding="utf-8"))
        self.assertEqual(proposal_service.get(proposal.proposal_id).status, "applied")

    def test_invalid_symbol_or_document_authority_never_persists(self) -> None:
        pack = self._pack()
        invalid = SemanticKnowledgeService(
            self.root, self._runtime(self._response(pack, bad_symbol=True), cache=True),
        )
        with self.assertRaises(FeatureGuideValidationError):
            invalid.generate_feature_guide(pack, persist=True)
        self.assertFalse((self.root / ".project-kb" / "drafts" / "features" / "bag-item-use.json").exists())
        with KnowledgeStore(self.project.db_path, readonly=True) as store:
            self.assertIsNone(store.get_knowledge("draft.feature.bag-item-use"))
        self.assertFalse(any((self.root / ".project-kb" / "provider-cache").glob("*.json")))

        (self.root / "docs").mkdir(exist_ok=True)
        (self.root / "docs" / "legacy.md").write_text("# 旧设计\n\n可能已过期。\n", encoding="utf-8")
        doc_pack = EvidencePackBuilder(self.root).build(
            "开发背包功能", ["docs/legacy.md", "src/bag.py", "tests/test_bag.py"],
        )
        invalid_doc = SemanticKnowledgeService(
            self.root, self._runtime(self._response(doc_pack, document_authority="source")),
        )
        with self.assertRaises(FeatureGuideValidationError):
            invalid_doc.generate_feature_guide(doc_pack, persist=True)

    def test_source_change_marks_draft_stale_and_candidates_are_source_traceable(self) -> None:
        pack = self._pack()
        SemanticKnowledgeService(self.root, self._runtime(self._response(pack))).generate_feature_guide(pack, persist=True)
        candidates = SemanticKnowledgeService(self.root).discover_feature_candidates()
        bag = next(item for item in candidates if item["domain"] == "bag.py")
        self.assertIn("src/bag.py", bag["sources"])

        (self.root / "src" / "bag.py").write_text(BAG_SOURCE + "\n# changed\n", encoding="utf-8")
        self.project.sync(task_summary="修改背包服务")
        record = KnowledgeAPI(self.root).get("draft.feature.bag-item-use")
        self.assertIn(record["status"], {"stale", "potentially_stale"})
        self.assertTrue(record["requires_live_source"])

    def test_feature_candidates_cli_returns_generated_source_anchors(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main([
                "feature-candidates", "--project", str(self.root), "--limit", "10", "--json",
            ])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["candidates"])
        self.assertTrue(all(item["requires_semantic_generation"] for item in payload["candidates"]))
        self.assertTrue(any("src/bag.py" in item["sources"] for item in payload["candidates"]))


if __name__ == "__main__":
    unittest.main()
