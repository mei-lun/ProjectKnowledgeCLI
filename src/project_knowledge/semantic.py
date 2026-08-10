from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .config import ProjectConfig
from .knowledge import KnowledgeGenerator
from .models import EvidencePack
from .provider import ModelRuntime
from .schemas import FEATURE_GUIDE_DRAFT_SCHEMA, validate_instance
from .store import KnowledgeStore
from .util import atomic_json, atomic_write, slug


class FeatureGuideValidationError(ValueError):
    """语义草案虽然符合结构 Schema，但不能由当前项目证据证明。"""


class FeatureGuideValidator:
    def __init__(self, root: str | Path, store: KnowledgeStore):
        self.root = Path(root).resolve()
        self.store = store

    def validate(self, guide: dict[str, Any], pack: EvidencePack) -> list[dict[str, Any]]:
        validate_instance(guide, FEATURE_GUIDE_DRAFT_SCHEMA)
        evidence = {item.path: item for item in pack.items}
        citations = list(_iter_citations(guide))
        orders = [int(step["order"]) for step in guide["workflow"]["steps"]]
        if orders != list(range(1, len(orders) + 1)):
            raise FeatureGuideValidationError("Workflow 步骤 order 必须从 1 开始连续递增")
        for citation in citations:
            relative = self._safe_path(str(citation["path"]))
            item = evidence.get(relative)
            if item is None:
                raise FeatureGuideValidationError(f"来源不在本次 EvidencePack 中：{relative}")
            candidate = self.root / relative
            if not candidate.is_file():
                raise FeatureGuideValidationError(f"来源文件不存在：{relative}")
            if relative.lower().endswith((".md", ".mdx", ".rst", ".txt")) and citation["authority"] != "candidate":
                raise FeatureGuideValidationError(f"已有文档只能作为 candidate 证据：{relative}")
            line_count = max(1, len(candidate.read_text(encoding="utf-8", errors="replace").splitlines()))
            if int(citation["line"]) > line_count:
                raise FeatureGuideValidationError(f"来源行号超出文件范围：{relative}:{citation['line']}")
            if citation["type"] == "file":
                if citation["hash"] != item.content_hash:
                    raise FeatureGuideValidationError(f"文件证据哈希不匹配：{relative}")
            else:
                symbol_id = citation.get("id")
                if not symbol_id:
                    raise FeatureGuideValidationError(f"符号来源缺少 id：{relative}")
                row = self.store.connection.execute(
                    "SELECT id, path, hash, line, end_line FROM symbols WHERE id = ?", (symbol_id,),
                ).fetchone()
                if row is None:
                    raise FeatureGuideValidationError(f"符号不存在：{symbol_id}")
                if row["path"] != relative:
                    raise FeatureGuideValidationError(f"符号路径不匹配：{symbol_id} -> {relative}")
                if row["hash"] != citation["hash"]:
                    raise FeatureGuideValidationError(f"符号哈希不匹配：{symbol_id}")
                end_line = row["end_line"] or row["line"]
                if not row["line"] <= int(citation["line"]) <= end_line:
                    raise FeatureGuideValidationError(f"符号行号不在定义范围内：{symbol_id}:{citation['line']}")
        return citations

    def _safe_path(self, raw: str) -> str:
        if not raw or Path(raw).is_absolute():
            raise FeatureGuideValidationError(f"来源必须是项目内相对路径：{raw!r}")
        candidate = (self.root / raw).resolve()
        try:
            return candidate.relative_to(self.root).as_posix()
        except ValueError as error:
            raise FeatureGuideValidationError(f"来源越过项目边界：{raw!r}") from error


class SemanticKnowledgeService:
    def __init__(self, root: str | Path, runtime: ModelRuntime | None = None):
        self.root = Path(root).resolve()
        self.config = ProjectConfig.load(self.root)
        self.runtime = runtime
        self.db_path = self.root / ".project-kb" / "index.db"

    def generate_feature_guide(self, pack: EvidencePack, *, persist: bool = True) -> dict[str, Any]:
        if self.runtime is None:
            raise RuntimeError("生成 Feature Guide 需要已配置的 ModelRuntime")
        if not self.db_path.exists():
            raise RuntimeError("项目尚未初始化；请先运行 project-kb init")
        with KnowledgeStore(self.db_path) as store:
            validator = FeatureGuideValidator(self.root, store)
            generation = self.runtime.generate(
                pack, FEATURE_GUIDE_DRAFT_SCHEMA,
                post_validate=lambda output: validator.validate(output, pack),
            )
            guide = generation.output
            feature_id = str(guide["feature_id"])
            record_id = f"draft.feature.{feature_id}"
            relative = f"{self.config.drafts_root}/features/{feature_id}.md"
            if persist:
                atomic_write(self.root / relative, render_feature_guide(guide, generation.to_dict()))
                atomic_json(
                    self.root / ".project-kb" / "drafts" / "features" / f"{feature_id}.json",
                    {**guide, "_generation": generation.to_dict()},
                )
                with store.transaction():
                    KnowledgeGenerator(self.root, self.config, store).generate(refresh_generated=False)
                record = store.get_knowledge(record_id)
                if record is None:
                    raise RuntimeError(f"Feature Guide 草案未进入知识索引：{record_id}")
        return {
            "record_id": record_id,
            "path": relative,
            "lifecycle": "draft",
            "persisted": persist,
            "guide": guide,
            "generation": generation.to_dict(),
        }

    def discover_feature_candidates(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            raise RuntimeError("项目尚未初始化；请先运行 project-kb init")
        with KnowledgeStore(self.db_path, readonly=True) as store:
            rows = store.rows(
                "SELECT module, path FROM files ORDER BY module, path"
            )
            symbols = store.rows(
                "SELECT id, name, kind, path FROM symbols "
                "WHERE kind IN ('class', 'function', 'method') ORDER BY path, line"
            )
        paths_by_domain: dict[str, list[str]] = defaultdict(list)
        symbols_by_path: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            paths_by_domain[str(row["module"])].append(str(row["path"]))
        for item in symbols:
            symbols_by_path[str(item["path"])].append(item)
        candidates: list[dict[str, Any]] = []
        for domain, paths in sorted(paths_by_domain.items()):
            anchors = [item for path in paths for item in symbols_by_path.get(path, [])]
            title = domain.replace("_", " ").replace("-", " ")
            candidates.append({
                "feature_id": slug(domain),
                "title": f"{title} 功能域候选",
                "domain": domain,
                "sources": paths,
                "symbol_anchors": [item["id"] for item in anchors[:20]],
                "confidence": "generated",
                "requires_semantic_generation": True,
            })
        return candidates[:max(1, min(limit, 500))]


def _iter_citations(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        sources = value.get("sources")
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict):
                    yield source
        for key, child in value.items():
            if key != "sources":
                yield from _iter_citations(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_citations(child)


def _source_markers(sources: list[dict[str, Any]]) -> str:
    markers: list[str] = []
    for source in sources:
        if source["type"] == "symbol":
            markers.append(f'<!-- project-kb:source symbol="{source["id"]}" -->')
        else:
            markers.append(f'<!-- project-kb:source file="{source["path"]}" -->')
    return " ".join(markers)


def _statement_lines(items: list[dict[str, Any]], *, ordered: bool = False) -> str:
    if not items:
        return "- 无"
    lines: list[str] = []
    for index, item in enumerate(items, 1):
        prefix = f"{item.get('order', index)}." if ordered else "-"
        lines.append(f"{prefix} {item['text']}  \n  {_source_markers(item['sources'])}")
    return "\n".join(lines)


def render_feature_guide(guide: dict[str, Any], generation: dict[str, Any]) -> str:
    recipe = guide["recipe"]
    unknowns = "\n".join(
        f"- **{item['text']}**：{item['reason']}；需要补充：{'、'.join(item['needed_evidence']) or '待确认'}"
        for item in guide["unknowns"]
    ) or "- 当前没有登记未决问题。"
    summary = guide["summary"]
    metadata = json.dumps({
        "schema": "feature-guide-draft-v1",
        "feature_id": guide["feature_id"],
        "lifecycle": "draft",
        "provider_id": generation["provider_id"],
        "model_id": generation["model_id"],
        "evidence_hash": generation["evidence_hash"],
    }, ensure_ascii=False, sort_keys=True)
    return f"""<!-- 本文件由 project-kb 语义生成器创建；当前是待审核草案，请勿直接视为已验证事实。 -->
<!-- project-kb:feature-guide {metadata} -->

# 功能指南：{guide['title']}

状态：`draft`（模型生成、来源已校验、尚未人工验证）  
功能域：`{guide['domain']}`

## 功能概述

{summary['text']}  
{_source_markers(summary['sources'])}

## 职责

{_statement_lines(guide['responsibilities'])}

## 入口

{_statement_lines(guide['entrypoints'])}

## 工作流：{guide['workflow']['title']}

{_statement_lines(guide['workflow']['steps'], ordered=True)}

## 依赖

{_statement_lines(guide['dependencies'])}

## 数据与状态

{_statement_lines(guide['data_and_state'])}

## 业务不变量

{_statement_lines(guide['invariants'])}

## 推荐扩展点

{_statement_lines(guide['extension_points'])}

## 开发步骤：{recipe['title']}

目标：{recipe['goal']}

### 前置条件

{_statement_lines(recipe['prerequisites'])}

### 实施

{_statement_lines(recipe['steps'], ordered=True)}

### 验证

{_statement_lines(recipe['verification'])}

### 回滚

{_statement_lines(recipe['rollback'])}

## 测试

{_statement_lines(guide['tests'])}

## 已知陷阱

{_statement_lines(guide['pitfalls'])}

## 未决问题

{unknowns}
"""
