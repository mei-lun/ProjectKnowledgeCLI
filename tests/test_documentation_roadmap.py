from __future__ import annotations

import json
import unittest
from pathlib import Path

from project_knowledge import __version__


class DocumentationRoadmapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.readme = (cls.root / "README.md").read_text(encoding="utf-8")

    def test_readme_is_project_neutral_and_only_describes_current_workflows(self) -> None:
        self.assertIn("CodeGraph", self.readme)
        self.assertIn(".project-kb/generated", self.readme)
        for obsolete_or_project_specific in (
            "gardenserver", "普通活动", "普通玩家功能", "登录模块",
            "1,295", "0.1.22", "已完成基础版", "旧版", "下一版本计划",
        ):
            self.assertNotIn(obsolete_or_project_specific, self.readme)

    def test_quick_start_does_not_recommend_legacy_watcher(self) -> None:
        quick_start = self.readme.split("## 快速开始", 1)[1].split("## 常用入口", 1)[0]
        self.assertNotIn("project-kb watch /path/to/repository", quick_start)
        self.assertIn("MCP", quick_start)
        self.assertIn("分别审核方法论和项目事实指导", quick_start)
        self.assertNotIn("尚未实现", quick_start)

    def test_readme_links_stable_reference_documents(self) -> None:
        self.assertIn("docs/compatibility-matrix.md", self.readme)
        self.assertIn("docs/project-knowledge-system-design.md", self.readme)
        self.assertIn("docs/project-knowledge-system-audit.md", self.readme)
        self.assertNotIn("docs/next-version-plan.md", self.readme)
        self.assertNotIn("docs/future-features.md", self.readme)

    def test_next_version_plan_is_scoped_and_testable(self) -> None:
        path = self.root / "docs" / "next-version-plan.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn("0.1.27", text)
        for requirement in ("REL-001", "CG-006", "RET-006"):
            self.assertIn(requirement, text)
        self.assertIn("验收标准", text)
        self.assertIn("0.1.28", text)

    def test_current_audit_records_wp11_release_evidence(self) -> None:
        audit = (self.root / "docs" / "project-knowledge-system-audit.md").read_text(encoding="utf-8")
        current = audit.split("## 当前交付复核", 1)[1].split("## ", 1)[0]
        for requirement in ("WP-11", "REL-001", "CG-006", "RET-006"):
            self.assertIn(requirement, current)
        self.assertNotIn("真实 CodeGraph Adapter 仍未完成", current)

    def test_readme_documents_release_finalization(self) -> None:
        self.assertIn("project-kb finalize", self.readme)

    def test_current_release_documents_match_package_version(self) -> None:
        audit = (self.root / "docs" / "project-knowledge-system-audit.md").read_text(encoding="utf-8")
        codegraph = (self.root / "docs" / "codegraph-evaluation-current.md").read_text(encoding="utf-8")

        self.assertIn(f"当前质量指标（{__version__}）", self.readme)
        self.assertIn(f"当前版本：{__version__}", audit)
        self.assertIn(f"当前 CodeGraph 评测状态（{__version__}）", codegraph)

    def test_readme_quality_summary_matches_active_codegraph_report(self) -> None:
        report = json.loads(
            (self.root / "evaluation" / "reports" / "latest.json").read_text(encoding="utf-8")
        )
        metrics = report["strategies"]["codegraph"]["metrics"]

        for metric in (
            "file_recall",
            "file_precision",
            "core_file_recall",
            "core_file_precision",
            "symbol_recall",
            "symbol_precision",
        ):
            self.assertIn(f"{metrics[metric]:.6f}", self.readme)
        self.assertIn("P95 延迟 | 见活动报告（环境相关）", self.readme)

    def test_retrieval_work_package_tracks_phase3_requirements(self) -> None:
        work_package = (self.root / "docs" / "retrieval-quality-work-package.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("WP-RQ-04", work_package)
        for requirement in (
            "RQ-P3-001",
            "RQ-P3-002",
            "RQ-P3-003",
            "RQ-P3-004",
            "RQ-P3-005",
        ):
            self.assertIn(requirement, work_package)
        self.assertNotIn("This Phase 0 batch", work_package)

    def test_codegraph_decision_preserves_evaluated_failure_reason(self) -> None:
        decision = (
            self.root / "docs" / "knowledge" / "decisions" / "0002-codegraph-adapter-boundary.md"
        ).read_text(encoding="utf-8")
        background = decision.split("## 背景", 1)[1].split("## 决策", 1)[0]
        self.assertIn("不允许以 builtin 静默冒充已选择的外部引擎", background)

    def test_future_features_distinguishes_candidates_from_commitments(self) -> None:
        path = self.root / "docs" / "future-features.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn("不代表开发承诺", text)
        self.assertIn("近期候选", text)
        self.assertIn("中期增强", text)
        self.assertIn("长期方向", text)
        self.assertIn("迁入下一版本", text)


if __name__ == "__main__":
    unittest.main()
