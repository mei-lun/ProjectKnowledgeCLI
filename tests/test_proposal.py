from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from project_knowledge.cli import main
from project_knowledge.models import PatchOperation
from project_knowledge.proposal import ProposalConflictError, ProposalService
from project_knowledge.service import ProjectService


class ProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("def create_item():\n    return 1\n", encoding="utf-8")
        (self.root / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
        ProjectService(self.root).initialize()
        self.architecture = self.root / ".project-kb" / "curated" / "architecture.md"
        self.architecture.write_text(
            "# 架构\n\n人工维护的边界，不得覆盖。\n\n"
            '<!-- project-kb:generated id="entrypoints" -->\n旧入口\n<!-- /project-kb:generated -->\n',
            encoding="utf-8",
        )
        self.proposals = ProposalService(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stable_id_pending_proposal_does_not_modify_curated_and_apply_is_scoped_and_idempotent(self) -> None:
        before = self.architecture.read_text(encoding="utf-8")
        operation = PatchOperation(op="upsert_generated_block", block_id="entrypoints", content="新入口：create_item")
        first = self.proposals.create(
            target=".project-kb/curated/architecture.md", reason="入口实现已更新",
            evidence=["src/app.py"], operations=[operation], confidence=0.9, change_range="HEAD",
        )
        second = self.proposals.create(
            target=".project-kb/curated/architecture.md", reason="入口实现已更新",
            evidence=["src/app.py"], operations=[operation], confidence=0.9, change_range="HEAD",
        )
        self.assertEqual(first.proposal_id, second.proposal_id)
        self.assertRegex(first.proposal_id, r"^kp-[0-9a-f]{16}$")
        self.assertEqual(self.architecture.read_text(encoding="utf-8"), before)
        self.assertEqual(self.proposals.pending_count(), 1)

        preview = self.proposals.apply(first.proposal_id, reviewer="mei", review_reason="证据充分", dry_run=True)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(self.architecture.read_text(encoding="utf-8"), before)
        applied = self.proposals.apply(first.proposal_id, reviewer="mei", review_reason="证据充分")
        content = self.architecture.read_text(encoding="utf-8")
        self.assertIn("人工维护的边界，不得覆盖。", content)
        self.assertIn("新入口：create_item", content)
        self.assertNotIn("旧入口", content)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["reviewer"], "mei")
        self.assertEqual(applied["review_reason"], "证据充分")
        self.assertTrue(applied["reviewed_at"])
        self.assertEqual(self.proposals.pending_count(), 0)

        unchanged = self.proposals.apply(first.proposal_id, reviewer="mei", review_reason="重复确认")
        self.assertTrue(unchanged["idempotent"])
        self.assertEqual(self.architecture.read_text(encoding="utf-8"), content)

    def test_reject_retains_audit_record_without_touching_target(self) -> None:
        before = self.architecture.read_text(encoding="utf-8")
        proposal = self.proposals.create(
            target=".project-kb/curated/architecture.md", reason="候选内容", evidence=["src/app.py"],
            operations=[PatchOperation(op="upsert_generated_block", block_id="entrypoints", content="候选入口")],
        )
        rejected = self.proposals.reject(proposal.proposal_id, reviewer="reviewer", review_reason="结论不成立")
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(self.architecture.read_text(encoding="utf-8"), before)
        stored = json.loads((self.root / ".project-kb" / "proposals" / f"{proposal.proposal_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["status"], "rejected")
        self.assertEqual(stored["review_reason"], "结论不成立")

    def test_changed_target_marks_proposal_conflicted_and_prevents_apply(self) -> None:
        proposal = self.proposals.create(
            target=".project-kb/curated/architecture.md", reason="更新入口", evidence=["src/app.py"],
            operations=[PatchOperation(op="upsert_generated_block", block_id="entrypoints", content="新入口")],
        )
        self.architecture.write_text(self.architecture.read_text(encoding="utf-8") + "\n并发人工修改。\n", encoding="utf-8")
        changed = self.architecture.read_text(encoding="utf-8")
        with self.assertRaises(ProposalConflictError):
            self.proposals.apply(proposal.proposal_id, reviewer="mei", review_reason="尝试应用")
        self.assertEqual(self.architecture.read_text(encoding="utf-8"), changed)
        stored = self.proposals.get(proposal.proposal_id)
        self.assertEqual(stored.status, "conflicted")
        self.assertIn("目标内容自提案生成后发生变化", stored.conflict_reason or "")

    def test_changed_evidence_marks_proposal_stale_even_when_target_is_unchanged(self) -> None:
        proposal = self.proposals.create(
            target=".project-kb/curated/architecture.md", reason="更新入口", evidence=["src/app.py"],
            operations=[PatchOperation(op="upsert_generated_block", block_id="entrypoints", content="新入口")],
        )
        (self.root / "src" / "app.py").write_text("def create_item():\n    return 2\n", encoding="utf-8")
        with self.assertRaises(ProposalConflictError):
            self.proposals.apply(proposal.proposal_id, reviewer="mei", review_reason="尝试应用")
        stored = self.proposals.get(proposal.proposal_id)
        self.assertEqual(stored.status, "conflicted")
        self.assertIn("提案来源自生成后发生变化", stored.conflict_reason or "")

    def test_delete_requires_supersedes_evidence_and_only_removes_named_block(self) -> None:
        with self.assertRaises(ValueError):
            self.proposals.create(
                target=".project-kb/curated/architecture.md", reason="删除旧入口", evidence=["src/app.py"],
                operations=[PatchOperation(op="delete_generated_block", block_id="entrypoints")],
            )
        proposal = self.proposals.create(
            target=".project-kb/curated/architecture.md", reason="旧入口已由新流程替代",
            evidence=["src/app.py", ".project-kb/curated/feature-guide-generation.md"],
            operations=[PatchOperation(
                op="delete_generated_block", block_id="entrypoints",
                supersedes=["feature.new-entrypoint"], deleted_sources=["src/legacy-entrypoint.py"],
            )],
        )
        self.proposals.apply(proposal.proposal_id, reviewer="mei", review_reason="替代关系已确认")
        content = self.architecture.read_text(encoding="utf-8")
        self.assertIn("人工维护的边界，不得覆盖。", content)
        self.assertNotIn('id="entrypoints"', content)

    def test_adr_operation_only_creates_new_draft_and_never_rewrites_existing_adr(self) -> None:
        accepted = self.root / ".project-kb" / "decisions" / "0001-local-first-core.md"
        accepted.parent.mkdir(parents=True, exist_ok=True)
        accepted.write_text("# ADR 0001\n\n状态：已接受\n", encoding="utf-8")
        accepted_before = accepted.read_text(encoding="utf-8")
        proposal = self.proposals.create(
            target=".project-kb/decisions/0002-proposal-review.md", reason="记录提案审核边界",
            evidence=["docs/project-knowledge-system-design.md"],
            operations=[PatchOperation(op="append_adr_draft", content="# ADR-0002：提案审核边界\n\n采用显式审核后应用。", supersedes=["decision.local-first-core"])],
        )
        self.proposals.apply(proposal.proposal_id, reviewer="mei", review_reason="创建待评审 ADR")
        draft = self.root / ".project-kb" / "decisions" / "0002-proposal-review.md"
        self.assertIn("状态：草案", draft.read_text(encoding="utf-8"))
        self.assertEqual(accepted.read_text(encoding="utf-8"), accepted_before)
        with self.assertRaises(ValueError):
            self.proposals.create(
                target=".project-kb/decisions/0001-local-first-core.md", reason="尝试改写已接受 ADR",
                evidence=["src/app.py"],
                operations=[PatchOperation(op="upsert_generated_block", block_id="decision", content="不允许")],
            )

    def test_cli_propose_apply_reject_support_json_quiet_and_dry_run(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main([
                "propose", "HEAD", "--project", str(self.root), "--target", ".project-kb/curated/architecture.md",
                "--reason", "CLI 更新入口", "--evidence", "src/app.py", "--operation", "upsert_generated_block",
                "--block-id", "entrypoints", "--content", "CLI 新入口", "--dry-run", "--json",
            ])
        self.assertEqual(exit_code, 0)
        self.assertTrue(json.loads(stdout.getvalue())["dry_run"])
        self.assertEqual(self.proposals.pending_count(), 0)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main([
                "propose", "HEAD", "--project", str(self.root), "--target", ".project-kb/curated/architecture.md",
                "--reason", "CLI 更新入口", "--evidence", "src/app.py", "--operation", "upsert_generated_block",
                "--block-id", "entrypoints", "--content", "CLI 新入口", "--json",
            ]), 0)
        proposal_id = json.loads(stdout.getvalue())["proposal_id"]
        self.assertEqual(main([
            "apply", proposal_id, "--project", str(self.root), "--reviewer", "mei", "--reason", "CLI 审核通过", "--quiet",
        ]), 0)

        other = self.proposals.create(
            target=".project-kb/curated/architecture.md", reason="拒绝候选", evidence=["src/app.py"],
            operations=[PatchOperation(op="upsert_generated_block", block_id="other", content="候选")],
        )
        self.assertEqual(main([
            "reject", other.proposal_id, "--project", str(self.root), "--reviewer", "mei", "--reason", "CLI 拒绝", "--quiet",
        ]), 0)


if __name__ == "__main__":
    unittest.main()
