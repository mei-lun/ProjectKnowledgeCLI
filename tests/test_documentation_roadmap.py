from __future__ import annotations

import unittest
from pathlib import Path


class DocumentationRoadmapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.readme = (cls.root / "README.md").read_text(encoding="utf-8")

    def test_readme_reports_current_release_and_codegraph_state(self) -> None:
        self.assertIn("0.1.22", self.readme)
        self.assertIn("CodeGraph", self.readme)
        self.assertIn("已完成", self.readme)
        self.assertIn(".project-kb/generated", self.readme)
        self.assertNotIn("外部 CodeGraph、多客户端和团队治理不是当前验收目标", self.readme)
        self.assertNotIn("版本化清单和 `docs/knowledge`", self.readme)

    def test_quick_start_does_not_recommend_legacy_watcher(self) -> None:
        quick_start = self.readme.split("## 快速开始", 1)[1].split("## 常用入口", 1)[0]
        self.assertNotIn("project-kb watch /path/to/repository", quick_start)
        self.assertIn("MCP", quick_start)
        self.assertIn("分别审核方法论和项目事实指导", quick_start)
        self.assertNotIn("尚未实现", quick_start)

    def test_readme_links_current_next_and_future_documents(self) -> None:
        self.assertIn("docs/next-version-plan.md", self.readme)
        self.assertIn("docs/future-features.md", self.readme)
        self.assertIn("docs/project-knowledge-system-audit.md", self.readme)

    def test_next_version_plan_is_scoped_and_testable(self) -> None:
        path = self.root / "docs" / "next-version-plan.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn("0.1.22", text)
        for requirement in ("NV-MODEL-001", "NV-INIT-001", "NV-MCP-001", "NV-INCR-001", "NV-VERIFY-001"):
            self.assertIn(requirement, text)
        self.assertIn("验收标准", text)
        self.assertIn("明确不进入", text)

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
