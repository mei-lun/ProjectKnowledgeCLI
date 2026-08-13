from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from project_knowledge.initialization import InitializationWorkflow


class FakeCodeGraph:
    def __init__(self, files):
        self._files = files

    def snapshot(self):
        files = sorted(self._files, key=lambda item: item["path"])
        identity = [
            {"path": item["path"], "language": item["language"], "content_hash": item["content_hash"]}
            for item in files
        ]
        snapshot_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"snapshot_id": snapshot_id, "files": files}


class InitializationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_stable_batches_resume_and_submit(self):
        files = [
            {"path": f"app/f{i:02}.lua", "language": "lua", "content_hash": f"h{i}", "module": "app", "symbols": []}
            for i in range(41)
        ] + [{"path": "lib/a.lua", "language": "lua", "content_hash": "ha", "module": "lib", "symbols": []}]
        client = FakeCodeGraph(files)
        workflow = InitializationWorkflow(self.root, client=client)
        first = workflow.start()
        self.assertEqual(first["total_files"], 42)
        self.assertEqual([len(item["files"]) for item in first["batches"]], [40, 1, 1])
        self.assertEqual(workflow.start()["run_id"], first["run_id"])
        batch = workflow.next_batch(first["run_id"])["batch"]
        path = batch["files"][0]
        result = workflow.submit_batch(first["run_id"], batch["batch_id"], first["snapshot_id"], [{
            "category_id": "activity", "name": "活动", "purpose": "开发活动", "confidence": 0.8,
            "evidence": [{"path": path, "hash": next(item["content_hash"] for item in files if item["path"] == path)}],
        }])
        self.assertEqual(result["covered_files"], 40)
        self.assertEqual(workflow.next_batch(first["run_id"])["batch"]["ordinal"], 1)

    def test_empty_project_is_ready(self):
        result = InitializationWorkflow(self.root, client=FakeCodeGraph([])).start()
        self.assertTrue(result["ready_for_category_draft"])
        self.assertEqual(result["status"], "category_review")

    def test_new_snapshot_reuses_only_unchanged_batches(self):
        files = [
            {"path": f"app/f{i:02}.lua", "language": "lua", "content_hash": f"h{i}", "module": "app", "symbols": []}
            for i in range(41)
        ]
        client = FakeCodeGraph(files)
        workflow = InitializationWorkflow(self.root, client=client)
        first = workflow.start()
        for batch in first["batches"]:
            evidence_path = batch["files"][0]
            workflow.submit_batch(first["run_id"], batch["batch_id"], first["snapshot_id"], [{
                "category_id": "activity", "name": "活动", "purpose": "开发活动", "confidence": .8,
                "evidence": [{"path": evidence_path, "hash": next(item["content_hash"] for item in files if item["path"] == evidence_path)}],
            }])
        client._files[0]["content_hash"] = "changed"
        second = workflow.start()
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual([item["status"] for item in second["batches"]], ["pending", "completed"])
        self.assertEqual(second["covered_files"], 1)

    def test_rejects_wrong_snapshot_and_evidence(self):
        client = FakeCodeGraph([{"path": "a.lua", "language": "lua", "content_hash": "h", "module": "", "symbols": []}])
        workflow = InitializationWorkflow(self.root, client=client)
        result = workflow.start()
        batch = result["batches"][0]
        with self.assertRaisesRegex(ValueError, "快照"):
            workflow.submit_batch(result["run_id"], batch["batch_id"], "old", [])
        with self.assertRaisesRegex(ValueError, "证据"):
            workflow.submit_batch(result["run_id"], batch["batch_id"], result["snapshot_id"], [{
                "category_id": "x", "name": "x", "purpose": "x", "confidence": .5,
                "evidence": [{"path": "other.lua", "hash": "h"}],
            }])
        with self.assertRaisesRegex(ValueError, "hash"):
            workflow.submit_batch(result["run_id"], batch["batch_id"], result["snapshot_id"], [{
                "category_id": "x", "name": "x", "purpose": "x", "confidence": .5,
                "evidence": [{"path": "a.lua", "hash": "old"}],
            }])


if __name__ == "__main__":
    unittest.main()
