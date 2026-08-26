from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_knowledge.mcp import MCPServer


class FakeCodeGraph:
    states: dict[str, dict[str, str]] = {}

    def __init__(self, project, *_args, **_kwargs):
        self.root = str(Path(project).resolve())
        self.states.setdefault(self.root, {"src/feature.lua": "sha256:h1"})

    def snapshot(self):
        files = [
            {
                "path": path,
                "language": "lua",
                "content_hash": content_hash,
                "module": "src",
                "symbols": [{"name": "feature_entry"}],
            }
            for path, content_hash in sorted(self.states[self.root].items())
        ]
        identity = [
            {"path": item["path"], "language": item["language"], "content_hash": item["content_hash"]}
            for item in files
        ]
        snapshot_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {"snapshot_id": snapshot_id, "files": files}

    def source(self, path, start_line=1, limit=400):
        return "local function feature_entry() return true end\n"

    def impact(self, symbol, depth=2):
        return {"affected": [{"filePath": "src/feature.lua", "name": str(symbol)}]}


class GuidanceEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        FakeCodeGraph.states.clear()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def call(server: MCPServer, name: str, arguments: dict):
        response = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        if response["result"].get("isError"):
            raise AssertionError(response["result"]["content"][0]["text"])
        return response["result"]["structuredContent"]

    def run_project(self, name: str):
        root = self.base / name
        (root / "src").mkdir(parents=True)
        (root / "src" / "feature.lua").write_text("return true\n", encoding="utf-8")
        server = MCPServer(root)

        started = self.call(server, "knowledge_initialization_start", {})
        batch = self.call(server, "knowledge_initialization_next", {"runId": started["run_id"]})
        file_hash = batch["file_facts"][0]["content_hash"]
        submitted = self.call(server, "knowledge_initialization_submit", {
            "runId": started["run_id"], "batchId": batch["batch"]["batch_id"],
            "snapshotId": started["snapshot_id"],
            "candidates": [{
                "category_id": "service-feature", "name": "服务功能", "purpose": "提供可复用服务能力",
                "confidence": 0.9, "evidence": [{"path": "src/feature.lua", "hash": file_hash}],
            }],
            "analyzedFiles": batch["batch"]["files"],
        })
        self.assertTrue(submitted["ready_for_category_draft"])

        catalog = {"categories": [{
            "category_id": "service-feature", "name": "服务功能", "purpose": "提供可复用服务能力",
            "applies_to": ["服务端功能"], "excludes": ["纯文档修改"],
            "samples": ["src/feature.lua"],
            "evidence": [{"path": "src/feature.lua", "hash": file_hash}],
            "confidence": 0.9, "unknowns": [], "relations": [],
        }]}
        draft = self.call(server, "knowledge_draft_save", {
            "action": "save", "kind": "category_catalog", "runId": started["run_id"], "content": catalog,
        })
        self.assertTrue(Path(draft["path"]).is_file())
        self.call(server, "knowledge_draft_confirm", {
            "draftId": draft["draft_id"], "contentHash": draft["content_hash"], "reviewer": "e2e",
        })

        """
        methodology = {
            "basic": {"title": "鏈嶅姟鍔熻兘杞婚噺鏂規硶璁?},
            "scope": ["鏈嶅姟绔姛鑳?], "questions": ["鍏ュ彛涓庤竟鐣?],
            "starter_checks": ["鎺ュ彛鍙祴璇?], "unknowns": [],
            "evidence": [{"path": "src/feature.lua", "hash": file_hash}],
        }
        """
        methodology = {
            "basic": {"title": "service methodology"},
            "scope": ["service feature"], "questions": ["entrypoints"],
            "starter_checks": ["run tests"], "unknowns": [],
            "evidence": [{"path": "src/feature.lua", "hash": file_hash}],
        }
        self.call(server, "knowledge_draft_save", {
            "action": "save", "kind": "methodology", "runId": started["run_id"],
            "categoryId": "service-feature", "content": methodology,
        })

        guide_content = {
            "basic": {"title": "服务功能开发指导"},
            "methodology_ref": {"id": "methodology.service-feature", "title": "服务功能轻量方法论"},
            "project_adaptation": {
                "entrypoints": ["src/feature.lua"], "locations": ["src"], "call_flow": ["入口到服务"],
                "registration": ["注册入口"], "data_and_config": ["显式配置"], "steps": ["实现服务"],
                "invariants": ["接口稳定"], "testing": ["运行项目测试"], "release": ["灰度发布"],
                "rollback": ["恢复旧实现"],
            },
            "variants": [], "evidence": [{"path": "src/feature.lua", "hash": file_hash}], "unknowns": [],
        }
        guide = self.call(server, "knowledge_draft_save", {
            "action": "save", "kind": "guidance", "runId": started["run_id"],
            "categoryId": "service-feature", "content": guide_content,
        })
        confirmed = self.call(server, "knowledge_draft_confirm", {
            "draftId": guide["draft_id"], "contentHash": guide["content_hash"], "reviewer": "e2e",
        })
        formal = self.call(server, "knowledge_get", {"id": "guide.service-feature"})
        self.assertEqual(formal["kind"], "development-guide")
        self.assertNotIn("第一层", formal["content"])
        self.assertIn("当前项目事实指导", formal["content"])

        FakeCodeGraph.states[str(root.resolve())]["src/feature.lua"] = "sha256:h2"
        changes = self.call(server, "knowledge_changes", {})
        self.assertEqual(changes["modified"], ["src/feature.lua"])
        updated = self.call(server, "knowledge_update_submit", {
            "changeId": changes["change_id"], "level": "fact", "categoryId": "service-feature",
            "content": {"guidance_unchanged": True},
            "evidence": [{"path": "src/feature.lua", "hash": "sha256:h2"}],
        })
        current = self.call(server, "knowledge_changes", {})
        self.assertEqual(current["status"], "current")
        return {
            "batches": len(started["batches"]), "guide_status": confirmed["status"],
            "update_level": updated["level"], "current": current["status"],
        }

    def test_two_different_project_names_follow_the_same_complete_workflow(self):
        with patch("project_knowledge.initialization.CodeGraphClient", FakeCodeGraph), patch(
            "project_knowledge.incremental.CodeGraphClient", FakeCodeGraph
        ), patch(
            "project_knowledge.mcp.CodeGraphClient", FakeCodeGraph
        ):
            first = self.run_project("alpha-service")
            second = self.run_project("unrelated-beta")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
