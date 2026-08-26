from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from project_knowledge.guidance_store import GuidanceStore
from project_knowledge.guidance_workflow import GuidanceWorkflow
from project_knowledge.initialization import InitializationWorkflow
from project_knowledge.retrieval import KnowledgeAPI
from project_knowledge.store import KnowledgeStore


class FakeCodeGraph:
    def __init__(self, files: list[dict[str, object]]):
        self.files = files

    def snapshot(self) -> dict[str, object]:
        files = sorted(self.files, key=lambda item: str(item["path"]))
        identity = [
            {
                "path": item["path"],
                "language": item["language"],
                "content_hash": item["content_hash"],
            }
            for item in files
        ]
        snapshot_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {"snapshot_id": snapshot_id, "files": files}


class GuidanceInitializationLoopTests(unittest.TestCase):
    def test_two_batches_two_categories_reach_ready_without_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [
                {
                    "path": f"app/f{index:02}.lua",
                    "language": "lua",
                    "content_hash": f"sha256:h{index:02}",
                    "module": "app",
                    "symbols": [],
                }
                for index in range(41)
            ]
            client = FakeCodeGraph(files)
            initialization = InitializationWorkflow(root, client=client)
            started = initialization.start()
            self.assertEqual([len(item["files"]) for item in started["batches"]], [40, 1])

            evidence_by_category: dict[str, dict[str, str]] = {}
            for index in range(2):
                current = initialization.next_batch(started["run_id"])
                batch = current["batch"]
                path = batch["files"][0]
                content_hash = next(
                    str(item["content_hash"])
                    for item in files
                    if item["path"] == path
                )
                category_id = "alpha" if index == 0 else "beta"
                evidence = {"path": path, "hash": content_hash}
                evidence_by_category[category_id] = evidence
                completed = initialization.submit_batch(
                    started["run_id"], batch["batch_id"], started["snapshot_id"],
                    [{
                        "category_id": category_id,
                        "name": category_id.title(),
                        "purpose": f"Develop {category_id} features",
                        "confidence": .9,
                        "evidence": [evidence],
                    }],
                    analyzed_files=batch["files"],
                )
            self.assertEqual(completed["next_action"], "create_category_draft")
            self.assertEqual(
                [item["category_id"] for item in completed["category_candidates"]],
                ["alpha", "beta"],
            )
            with KnowledgeStore(root / ".project-kb" / "index.db") as store:
                status = KnowledgeAPI._guidance_workflow_status(
                    store, current_snapshot_id=started["snapshot_id"]
                )
                self.assertEqual(status["next_action"], "create_category_draft")
                self.assertEqual(
                    [item["category_id"] for item in status["category_candidates"]],
                    ["alpha", "beta"],
                )

            workflow = GuidanceWorkflow(root, client=client)
            catalog = {"categories": [
                {
                    "category_id": category_id,
                    "name": category_id.title(),
                    "purpose": f"Develop {category_id} features",
                    "applies_to": [category_id],
                    "excludes": [],
                    "samples": [evidence_by_category[category_id]["path"]],
                    "evidence": [evidence_by_category[category_id]],
                    "confidence": .9,
                    "unknowns": [],
                    "relations": [],
                }
                for category_id in ("alpha", "beta")
            ]}
            workflow.save_draft("category_catalog", started["run_id"], catalog)

            for category_id in ("alpha", "beta"):
                workflow.save_draft("methodology", started["run_id"], {
                    "basic": {"title": f"{category_id.title()} methodology"},
                    "scope": [f"Use for {category_id}"],
                    "questions": ["Where is the entrypoint?"],
                    "starter_checks": ["Locate the owning module"],
                    "unknowns": [],
                }, category_id)
                workflow.save_draft("guidance", started["run_id"], {
                    "basic": {"title": f"{category_id.title()} guide"},
                    "methodology_ref": {
                        "id": f"methodology.{category_id}",
                        "title": f"{category_id.title()} methodology",
                    },
                    "project_adaptation": {
                        "entrypoints": [evidence_by_category[category_id]["path"]],
                        "locations": ["app"],
                        "call_flow": ["entry to handler"],
                        "registration": ["register handler"],
                        "data_and_config": ["module config"],
                        "steps": ["implement handler"],
                        "invariants": ["keep interface stable"],
                        "testing": ["run focused tests"],
                        "release": ["verify before release"],
                        "rollback": ["restore previous handler"],
                    },
                    "variants": [],
                    "evidence": [evidence_by_category[category_id]],
                    "unknowns": [],
                }, category_id)

            db = root / ".project-kb" / "index.db"
            with KnowledgeStore(db) as store:
                guidance = GuidanceStore(store)
                status = KnowledgeAPI._guidance_workflow_status(
                    store, current_snapshot_id=started["snapshot_id"]
                )
                self.assertEqual(status["state"], "ready")
                self.assertEqual(status["next_action"], "none")
                self.assertEqual(status["drafts"]["awaiting_confirmation"], 5)
                self.assertEqual(guidance.get_run(started["run_id"]).status, "complete")
                records = [
                    record for record in store.all_knowledge()
                    if record.ownership == "draft"
                ]
                self.assertEqual(len(records), 5)
                self.assertTrue(all(record.sources for record in records))
                self.assertTrue(all(record.confidence != "verified" for record in records))
                alpha_guide_id = next(
                    record.id for record in records if record.title == "Alpha项目事实指导草稿"
                )

            api = KnowledgeAPI(root)
            api.service.status = lambda **_kwargs: {"pending_files": []}
            searched = api.search(
                "Alpha guide", kinds=["development-guide"], _status={"pending_files": []}
            )
            self.assertTrue(any(item["id"] == alpha_guide_id for item in searched["results"]))
            fetched = api.get(alpha_guide_id)
            self.assertEqual(fetched["ownership"], "draft")
            self.assertTrue(fetched["requires_live_source"])


if __name__ == "__main__":
    unittest.main()
