from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .guidance_store import GuidanceStore
from .service import ProjectService
from .store import KnowledgeStore
from .util import run_git


def git_changed_paths(root: Path) -> list[str]:
    paths: set[str] = set()
    for arguments in (
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        output = run_git(root, *arguments)
        if output:
            paths.update(line.replace("\\", "/") for line in output.splitlines() if line)
    return sorted(paths)


class FinalizationService:
    def __init__(self, project: str | Path = ".") -> None:
        self.service = ProjectService(project)

    def finalize(self, check_only: bool = False) -> tuple[dict[str, Any], bool]:
        before = self.service.status()
        changed = git_changed_paths(self.service.root)
        non_generated = [path for path in changed if not self.service._is_generated_output(path)]
        if non_generated:
            return self._result(
                "source_commit_required",
                before,
                blocking_files=non_generated,
                next_action="Commit non-generated source changes, then run finalize again.",
            ), False

        if not before.get("verification_aligned"):
            if check_only:
                return self._result(
                    "sync_required",
                    before,
                    next_action="Run project-kb finalize without --check to synchronize the current source commit.",
                ), False
            self.service.sync(task_summary="release finalization")

        after = self.service.status()
        generated = [
            path for path in git_changed_paths(self.service.root)
            if self.service._is_generated_output(path)
        ]
        if generated:
            return self._result(
                "generated_commit_required",
                after,
                generated_files=generated,
                next_action="Review and commit the listed generated outputs, then run finalize --check.",
            ), False

        counts = after.get("counts", {})
        healthy = (
            bool(after.get("verification_aligned"))
            and counts.get("stale_knowledge", 0) == 0
            and counts.get("conflicted_knowledge", 0) == 0
        )
        guidance_blockers = self._guidance_blockers()
        healthy = healthy and not guidance_blockers
        status = "ready" if healthy else "knowledge_review_required"
        next_action = (
            "Release evidence is aligned."
            if healthy
            else guidance_blockers[0]["next_action"] if guidance_blockers
            else "Review stale or conflicted curated knowledge before release."
        )
        result = self._result(status, after, next_action=next_action)
        result["gate_blockers"] = guidance_blockers
        return result, healthy

    def _guidance_blockers(self) -> list[dict[str, str]]:
        blockers: list[dict[str, str]] = []
        try:
            snapshot = self.service.engine.snapshot(self.service.root, self.service.config)
            current_snapshot_id = snapshot.snapshot_id
        except RuntimeError:
            return [{
                "code": "codegraph_unavailable",
                "detail": "CodeGraph snapshot is unavailable.",
                "next_action": "Restore CodeGraph availability, then run finalize again.",
            }]
        with KnowledgeStore(self.service.db_path, readonly=True) as store:
            guidance = GuidanceStore(store)
            row = store.connection.execute(
                "SELECT run_id FROM guidance_runs ORDER BY updated_at DESC, created_at DESC, run_id DESC LIMIT 1"
            ).fetchone()
            run = guidance.get_run(str(row["run_id"])) if row else None
            if run is None:
                return [{
                    "code": "guidance_initialization_required",
                    "detail": "No guidance initialization run exists.",
                    "next_action": "Run knowledge_initialization_start and complete the guidance draft chain.",
                }]
            if run.status != "complete":
                blockers.append({
                    "code": "guidance_run_incomplete",
                    "detail": f"Latest guidance run status is {run.status}.",
                    "next_action": "Follow guidance_workflow.next_action until the run is complete.",
                })
            if run.covered_files != run.total_files or run.uncovered_files:
                blockers.append({
                    "code": "initialization_coverage_incomplete",
                    "detail": "Initialization batches do not cover every current file successfully.",
                    "next_action": "Complete or retry every pending/failed initialization batch.",
                })
            categories = guidance.list_categories(run.run_id)
            if not categories:
                blockers.append({
                    "code": "guidance_categories_required",
                    "detail": "No guidance categories exist for the latest run.",
                    "next_action": "Generate and save a category catalog draft.",
                })
            for category in categories:
                for asset_type, label in (("methodology", "methodology"), ("project_guidance", "guidance")):
                    version = guidance.current_version(category.category_id, asset_type)
                    if version is None or not version.evidence:
                        blockers.append({
                            "code": f"{label}_missing",
                            "detail": f"Category {category.category_id} has no evidence-backed current {label}.",
                            "next_action": f"Generate and confirm the {label} draft for {category.category_id}.",
                        })
            incomplete = guidance.list_pending_drafts(run.run_id)
            if any(draft.status == "incomplete" for draft in incomplete):
                blockers.append({
                    "code": "incomplete_draft",
                    "detail": "An incomplete guidance draft remains.",
                    "next_action": "Complete or reject every incomplete draft before finalization.",
                })
            if guidance.pending_changes():
                blockers.append({
                    "code": "pending_guidance_change",
                    "detail": "A guidance change has not completed all affected categories.",
                    "next_action": "Run knowledge_changes and process pending categories in order.",
                })
            baseline_raw = store.get_meta("guidance_snapshot")
            baseline_id = None
            if baseline_raw:
                try:
                    baseline_id = json.loads(baseline_raw).get("snapshot_id")
                except (TypeError, ValueError):
                    baseline_id = None
            if baseline_id != current_snapshot_id:
                blockers.append({
                    "code": "guidance_baseline_mismatch",
                    "detail": "Guidance baseline does not match the current CodeGraph snapshot.",
                    "next_action": "Process the current incremental change set before finalization.",
                })
        return blockers

    @staticmethod
    def _result(
        status: str,
        project_status: dict[str, Any],
        *,
        blocking_files: list[str] | None = None,
        generated_files: list[str] | None = None,
        next_action: str,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "head_commit": project_status.get("head_commit"),
            "index_commit": project_status.get("index_commit"),
            "commit_aligned": bool(project_status.get("commit_aligned")),
            "verification_aligned": bool(project_status.get("verification_aligned")),
            "blocking_files": blocking_files or [],
            "generated_files": generated_files or [],
            "next_action": next_action,
        }
