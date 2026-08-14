from __future__ import annotations

from pathlib import Path
from typing import Any

from .service import ProjectService
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
        status = "ready" if healthy else "knowledge_review_required"
        next_action = (
            "Release evidence is aligned."
            if healthy
            else "Review stale or conflicted curated knowledge before release."
        )
        return self._result(status, after, next_action=next_action), healthy

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
