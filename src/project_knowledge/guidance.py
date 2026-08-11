from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .codegraph import CodeGraphClient
from .gardenserver import CATEGORIES, GuidanceEvidenceCollector
from .guidance_templates import GENERAL_METHODOLOGY
from .util import atomic_json, atomic_write, utc_now


GUIDANCE_FILENAMES = {
    "activity-development": "普通活动开发.md",
    "player-feature-development": "普通玩家功能开发.md",
    "login-module-development": "登录模块开发.md",
}


class GuidanceService:
    """Generate two-layer, Chinese, evidence-backed guidance in one directory."""

    def __init__(self, root: str | Path, *, client: CodeGraphClient | None = None) -> None:
        self.root = Path(root).resolve()
        self.kb_root = self.root / ".project-kb"
        self.client = client

    def generate(self, categories: Iterable[str] | None = None, *, persist: bool = True) -> dict[str, Any]:
        selected = list(categories or CATEGORIES)
        unknown = sorted(set(selected) - set(CATEGORIES))
        if unknown:
            raise ValueError("未知指导类别：" + "、".join(unknown))
        evidence = GuidanceEvidenceCollector(self.root, self.client).collect_all()
        documents = {category: self._build_document(category, evidence[category]) for category in selected}
        result = {"categories": selected, "documents": documents, "generated_at": utc_now(), "status": "fresh"}
        if persist:
            self._persist(evidence, documents, result["generated_at"])
        return result

    def _build_document(self, category: str, evidence: dict[str, Any]) -> dict[str, Any]:
        method = GENERAL_METHODOLOGY[category]
        facts = evidence.get("facts", [])
        facts_by_kind: dict[str, list[dict[str, Any]]] = {}
        for fact in facts:
            facts_by_kind.setdefault(str(fact["kind"]), []).append(fact)
        adaptation = [
            {"text": f"在 `{fact['path']}:{fact['line']}` 可观察到：{fact['text']}", "source": fact}
            for fact in facts[:80]
        ]
        unknowns: list[str] = []
        if not facts:
            unknowns.append("当前样本没有采集到可验证的项目事实")
        if category == "activity-development" and not any("activity" in item["path"].lower() for item in facts):
            unknowns.append("未发现明确的统一活动目录或活动基类，需要人工确认项目是否采用分散式活动实现")
        return {
            "category": category,
            "title": method["title"],
            "layer_1_methodology": {
                "summary": method["summary"],
                "steps": list(method["steps"]),
                "checks": list(method["checks"]),
                "status": "generated",
            },
            "layer_2_project_adaptation": {
                "project": self.root.name,
                "samples": evidence.get("samples", []),
                "facts_by_kind": facts_by_kind,
                "observed_steps": adaptation,
                "status": "generated" if facts else "inferred",
            },
            "evidence": facts,
            "unknowns": unknowns,
            "source_hashes": evidence.get("source_hashes", {}),
        }

    def _persist(self, evidence: dict[str, dict[str, Any]], documents: dict[str, dict[str, Any]], generated_at: str) -> None:
        for legacy_category in GUIDANCE_FILENAMES:
            (self.kb_root / "generated" / f"{legacy_category}.md").unlink(missing_ok=True)
        for directory in ("evidence", "methodology", "guides", "generated", "logs"):
            (self.kb_root / directory).mkdir(parents=True, exist_ok=True)
        for category, pack in evidence.items():
            atomic_json(self.kb_root / "evidence" / f"{category}.json", pack)
        for category, document in documents.items():
            slug = category
            atomic_json(self.kb_root / "methodology" / f"{slug}.json", document["layer_1_methodology"])
            atomic_json(self.kb_root / "guides" / f"{slug}.json", document["layer_2_project_adaptation"])
            atomic_write(self.kb_root / "generated" / GUIDANCE_FILENAMES[category], self._render(document, generated_at))
        index_lines = ["<!-- 本文件由 project-kb 自动生成，请勿手动编辑。 -->", "# 开发指导索引", "", f"生成时间：`{generated_at}`", "", "| 指导类别 | 文档 | 项目样本 | 状态 |", "| --- | --- | --- | --- |"]
        for category, document in documents.items():
            index_lines.append(f"| {document['title']} | [{document['title']}]({GUIDANCE_FILENAMES[category]}) | {'、'.join(document['layer_2_project_adaptation']['samples']) or '无'} | `{document['layer_2_project_adaptation']['status']}` |")
        atomic_write(self.kb_root / "generated" / "开发指导索引.md", "\n".join(index_lines) + "\n")
        manifest_path = self.kb_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        manifest["guidance"] = {"categories": sorted(documents), "generated_at": generated_at, "root": ".project-kb/generated"}
        atomic_json(manifest_path, manifest)

    def _render(self, document: dict[str, Any], generated_at: str) -> str:
        method = document["layer_1_methodology"]
        adaptation = document["layer_2_project_adaptation"]
        lines = [
            "<!-- 本文件由 project-kb 自动生成，请勿手动编辑。 -->",
            f"# {document['title']}指导", "", f"生成时间：`{generated_at}`；项目：`{adaptation['project']}`", "",
            "## 第一层：可迁移方法论", "", method["summary"], "", "### 开发步骤", "",
        ]
        lines.extend(f"{index}. {step}" for index, step in enumerate(method["steps"], 1))
        lines.extend(["", "### 通用检查项", ""])
        lines.extend(f"- {item}" for item in method["checks"])
        lines.extend(["", "## 第二层：项目适配", "", f"当前项目样本：{'、'.join(adaptation['samples']) or '未指定'}", ""])
        if adaptation["observed_steps"]:
            lines.append("### 当前代码事实")
            lines.append("")
            for item in adaptation["observed_steps"]:
                source = item["source"]
                lines.append(f"- {item['text']}（来源：`{source['path']}:{source['line']}`，来源类型：`{source['source']}`）")
        else:
            lines.append("- 未采集到当前项目事实，不能据此编写项目特例。")
        lines.extend(["", "## 待人工确认", ""])
        lines.extend(f"- {item}" for item in document["unknowns"]) if document["unknowns"] else lines.append("- 当前没有登记未决问题。")
        lines.extend(["", "## 事实来源", ""])
        lines.extend(f"- `{item['path']}:{item['line']}`：{item['text']}（`{item['source']}`）" for item in document["evidence"][:120])
        return "\n".join(lines) + "\n"
