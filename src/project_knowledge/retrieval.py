from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .guidance_store import GuidanceStore
from .models import KnowledgeRecord
from .service import ProjectService
from .store import KnowledgeStore
from .util import approx_tokens, trim_to_tokens, utc_now


CONFIDENCE_WEIGHT = {"verified": 1.0, "generated": 0.8, "inferred": 0.3}
FRESHNESS_WEIGHT = {"fresh": 1.0, "potentially_stale": -0.5, "stale": -1.5, "conflicted": -2.0}

# 中文功能任务常使用业务短语，而源码使用英文 snake_case 标识符。
# 每个映射都先生成完整组合，再生成分词，便于精确命中优先且保持可解释。
IDENTIFIER_PHRASE_TOKENS = (
    ("初始化", ("initialize",)),
    ("配置模式", ("config", "schema")),
    ("所有权标记", ("marker", "update")),
    ("补丁升级", ("bump", "patch", "version")),
    ("核心版本", ("read", "project", "version")),
    ("客户端", ("client",)),
    ("安装", ("install",)),
    ("卸载", ("uninstall",)),
    ("迁移", ("migrate",)),
    ("读取", ("read",)),
    ("版本", ("version",)),
)


class KnowledgeAPI:
    def __init__(self, project: str | Path = "."):
        self.service = ProjectService(project)
        self.root = self.service.root
        self.config = ProjectConfig.load(self.root)
        if not self.service.db_path.exists():
            raise RuntimeError(f"{self.root} is not initialized")

    def status(self) -> dict[str, Any]:
        status = self.service.status()
        with KnowledgeStore(self.service.db_path) as store:
            status["guidance_workflow"] = self._guidance_workflow_status(store)
        return status

    @staticmethod
    def classify_task(task: str) -> dict[str, Any]:
        text = task.lower()
        for task_type, signals in [
            ("new_feature", ("新增", "开发", "实现", "添加", "扩展", "类似功能", "add", "new", "implement", "feature", "extend")),
            ("bug_fix", ("修复", "缺陷", "错误", "异常", "bug", "fix", "error", "exception")),
            ("refactor", ("重构", "迁移", "整理", "refactor", "migrate", "cleanup")),
            ("impact_analysis", ("影响", "依赖", "调用链", "改动范围", "impact", "dependency", "change scope")),
        ]:
            matched = [signal for signal in signals if signal in text]
            if matched:
                return {"task_type": task_type, "confidence": 0.9 if len(matched)>1 else 0.75, "signals": matched[:8], "rationale": f"任务命中{task_type}信号（{'、'.join(matched[:4])}），按对应开发指导流程组织检索。"}
        return {"task_type": "investigation", "confidence": 0.45, "signals": [], "rationale": "未命中特定开发意图，按调查与源码核验流程提供候选知识。"}

    def get(self, record_id: str) -> dict[str, Any]:
        pending = set(self.service.status().get("pending_files", []))
        with KnowledgeStore(self.service.db_path) as store:
            record = store.get_knowledge(record_id)
            if record is None:
                raise KeyError(f"unknown knowledge id: {record_id}")
            result = record.to_dict(include_content=True)
            pending_sources = self._pending_sources(record, pending)
            if pending_sources:
                result.pop("content", None)
                result["status"] = "potentially_stale"
                result["withheld"] = f"Content depends on pending source: {', '.join(pending_sources)}"
            pending_draft = self._pending_guidance_draft(store, record)
            if pending_draft:
                result["status"] = "potentially_stale"
                result["freshness"] = "potentially_stale"
                result["draft_id"] = pending_draft["draft_id"]
                result["draft_path"] = pending_draft["path"]
            result["requires_live_source"] = (
                bool(pending_sources) or bool(pending_draft) or record.status != "fresh"
                or record.confidence == "inferred" or record.ownership == "draft"
            )
            return result

    def search(self, query: str, kinds: list[str] | None = None, module: str | None = None, limit: int = 10) -> dict[str, Any]:
        started = time.monotonic()
        pending = set(self.service.status().get("pending_files", []))
        with KnowledgeStore(self.service.db_path) as store:
            matches = store.search_knowledge(query, max(1, min(limit * 2, 100)), kinds, module)
            seen_ids = {record.id for record, _ in matches}
            for record in store.all_knowledge():
                if record.id in seen_ids or record.kind not in {"feature-guide", "development-guide"}:
                    continue
                if kinds and record.kind not in kinds:
                    continue
                if module and module not in record.tags and f"/{module}/" not in record.path:
                    continue
                feature_title = re.sub(r"^功能指南[：:]\s*", "", record.title).strip()
                if feature_title and (feature_title in query or query in feature_title):
                    matches.append((record, 3.0))
                    seen_ids.add(record.id)
            ranked: list[tuple[KnowledgeRecord, float, float]] = []
            for record, text_score in matches:
                score = text_score + CONFIDENCE_WEIGHT.get(record.confidence, 0) + FRESHNESS_WEIGHT.get(record.status, -1)
                if module and module in record.tags:
                    score += 0.5
                if record.kind in {"feature-guide", "development-guide"}:
                    score += 2.0
                ranked.append((record, score, text_score))
            ranked.sort(key=lambda item: (-item[1], item[0].id))
            items = []
            for record, score, text_score in ranked[:limit]:
                pending_sources = self._pending_sources(record, pending)
                summary = (
                    f"[withheld: depends on pending source {', '.join(pending_sources)}]"
                    if pending_sources else self._summary(record.content)
                )
                breakdown = self._score_breakdown(record, text_score, module)
                pending_draft = self._pending_guidance_draft(store, record)
                items.append({
                    "id": record.id, "title": record.title, "kind": record.kind, "path": record.path,
                    "ownership": record.ownership, "confidence": record.confidence,
                    "freshness": "potentially_stale" if pending_sources or pending_draft else record.status,
                    **({"draft_id": pending_draft["draft_id"], "draft_path": pending_draft["path"]} if pending_draft else {}),
                    "score": round(score, 4), "summary": summary,
                    "text_match": round(text_score, 4), "score_breakdown": breakdown,
                    "why_selected": self._why_selected(record, breakdown),
                    "sources": [source.to_dict() for source in record.sources],
                    "requires_live_source": (
                        bool(pending_sources) or bool(pending_draft) or record.status != "fresh"
                        or record.confidence == "inferred" or record.ownership == "draft"
                    ),
                })
            result = {"query": query, "results": items, "gaps": [] if items else ["No matching knowledge record; search live source."]}
            self._record_query(store, "knowledge_search", len(query), result, started)
            return result

    def impact(self, files: list[str] | None = None, symbols: list[str] | None = None, max_hops: int = 1, max_relations: int = 500) -> dict[str, Any]:
        started = time.monotonic()
        files = [Path(path).as_posix().lstrip("./") for path in (files or [])]
        symbols = symbols or []
        max_hops = max(0, min(int(max_hops), 5))
        max_relations = max(1, min(int(max_relations), 5000))
        with KnowledgeStore(self.service.db_path) as store:
            symbol_ids = set(symbols)
            if files:
                placeholders = ",".join("?" for _ in files)
                symbol_ids.update(row["id"] for row in store.rows(f"SELECT id FROM symbols WHERE path IN ({placeholders})", files))
            expanded = set(symbol_ids)
            relations: list[dict[str, Any]] = []
            frontier = set(symbol_ids)
            relation_seen: set[tuple[str, str, str, int]] = set()
            for hop in range(1, max_hops + 1):
                if not frontier or len(relations) >= max_relations:
                    break
                ordered_frontier = sorted(frontier)
                placeholders = ",".join("?" for _ in ordered_frontier)
                rows = store.rows(
                    f"SELECT source, target, kind, path, line, confidence, resolved FROM relations WHERE source IN ({placeholders}) OR target IN ({placeholders}) ORDER BY confidence DESC, source, target LIMIT ?",
                    [*ordered_frontier, *ordered_frontier, max_relations - len(relations)],
                )
                next_frontier: set[str] = set()
                for relation in rows:
                    key = (relation["source"], relation["target"], relation["kind"], hop)
                    if key in relation_seen:
                        continue
                    relation_seen.add(key)
                    relation["hop"] = hop
                    relations.append(relation)
                    expanded.add(relation["source"])
                    if relation["resolved"]:
                        expanded.add(relation["target"])
                        if hop < max_hops:
                            next_frontier.add(relation["target"])
                frontier = next_frontier
            impacted_paths = set(files)
            if expanded:
                placeholders = ",".join("?" for _ in expanded)
                impacted_paths.update(row["path"] for row in store.rows(f"SELECT DISTINCT path FROM symbols WHERE id IN ({placeholders})", expanded))
            modules: list[str] = []
            tests: list[str] = []
            if impacted_paths:
                placeholders = ",".join("?" for _ in impacted_paths)
                modules = [row["module"] for row in store.rows(f"SELECT DISTINCT module FROM files WHERE path IN ({placeholders}) ORDER BY module", impacted_paths)]
                if modules:
                    module_marks = ",".join("?" for _ in modules)
                    tests = [row["path"] for row in store.rows(
                        f"SELECT path FROM files WHERE module IN ({module_marks}) AND (path LIKE '%test%' OR path LIKE '%spec%') ORDER BY path", modules
                    )]
            knowledge: list[dict[str, Any]] = []
            for record in store.all_knowledge():
                source_keys = {source.path or source.id for source in record.sources}
                if source_keys.intersection(impacted_paths | expanded):
                    knowledge.append({
                        "id": record.id, "title": record.title, "path": record.path,
                        "freshness": record.status, "confidence": record.confidence,
                    })
            result = {
                "input": {"files": files, "symbols": symbols},
                "max_hops": max_hops,
                "max_relations": max_relations,
                "affected_files": sorted(impacted_paths),
                "affected_symbols": sorted(expanded),
                "affected_modules": modules,
                "affected_tests": tests,
                "affected_knowledge": knowledge,
                "relations": relations,
                "relation_hops": {str(hop): sum(1 for relation in relations if relation.get("hop") == hop) for hop in range(1, max_hops + 1)},
                "impact_explanation": self._impact_explanation(files, symbols, max_hops, relations, modules, tests),
                "truncated": len(relations) >= max_relations,
                "limitations": self.service.engine.status()["limitations"],
            }
            self._record_query(store, "knowledge_impact", len(json.dumps(result["input"])), result, started)
            return result

    def context(self, task: str, max_tokens: int | None = None) -> dict[str, Any]:
        started = time.monotonic()
        budget = max(256, min(max_tokens or self.config.max_tokens, 50_000))
        status = self.status()
        intent = self.classify_task(task)
        search = self.search(task, limit=10)
        broad_project_requested = any(
            phrase in task.lower()
            for phrase in ["project map", "repository overview", "project overview", "项目地图", "项目概览"]
        )
        selected_results = [
            item for item in search["results"]
            if broad_project_requested or item["kind"] != "project"
        ][:4]
        terms = self._symbol_terms(task)
        with KnowledgeStore(self.service.db_path) as store:
            symbol_matches: list[dict[str, Any]] = []
            seen: set[str] = set()
            for term in terms[:12]:
                rows = store.rows(
                    "SELECT id, name, kind, path, line, confidence, "
                    "CASE WHEN name = ? OR id LIKE ? THEN 0 WHEN name LIKE ? THEN 1 ELSE 2 END AS match_rank "
                    "FROM symbols "
                    "WHERE name LIKE ? OR id LIKE ? "
                    "ORDER BY match_rank, confidence DESC, LENGTH(name), id LIMIT 3",
                    [term, f"%::{term}", f"{term}%", f"%{term}%", f"%{term}%"],
                )
                best_rank = min((row["match_rank"] for row in rows), default=2)
                for row in rows:
                    if best_rank == 0 and row["match_rank"] != 0:
                        continue
                    row.pop("match_rank", None)
                    if row["id"] not in seen:
                        seen.add(row["id"])
                        symbol_matches.append(row)
            impact = self.impact(symbols=[item["id"] for item in symbol_matches[:10]], max_hops=2 if intent["task_type"] in {"new_feature", "impact_analysis"} else 1, max_relations=200) if symbol_matches else {
                "affected_modules": [], "affected_tests": [], "affected_files": [], "affected_knowledge": []
            }
            reference_implementations = self._reference_implementations(symbol_matches, selected_results)
            extension_points = self._extension_points(symbol_matches)
            verification = self._verification_commands()
            fragments: list[dict[str, Any]] = []
            remaining = budget
            for item in selected_results:
                record = store.get_knowledge(item["id"])
                if not record:
                    continue
                pending_sources = self._pending_sources(record, set(status.get("pending_files", [])))
                fragment_budget = min(900, max(120, remaining // max(1, 12 - len(fragments))))
                content = "" if pending_sources else self._relevant_excerpt(record.content, task, fragment_budget)
                cost = approx_tokens(content)
                if cost > remaining:
                    continue
                fragments.append({
                    "id": record.id, "title": record.title, "kind": record.kind,
                    "confidence": record.confidence,
                    "freshness": "potentially_stale" if pending_sources else record.status,
                    "content": content, "sources": [source.to_dict() for source in record.sources],
                    "next_step": "Read live sources before relying on this record." if pending_sources or record.status != "fresh" else "Use cited symbols or files as the next source anchors.",
                    "requires_live_source": (
                        bool(pending_sources) or record.status != "fresh"
                        or record.confidence == "inferred" or record.ownership == "draft"
                    ),
                    "withheld_sources": pending_sources,
                    "tokens": cost,
                })
                remaining -= cost
            gaps: list[str] = list(search["gaps"])
            if status.get("pending_files"):
                gaps.append("The index has pending source changes; synchronize or read those files live.")
            if not symbol_matches:
                gaps.append("No exact symbol anchor matched the task terms.")
            unknowns = list(gaps)
            if not symbol_matches:
                unknowns.append("未找到精确符号锚点，需读取实时源码确认扩展位置。")
            if impact.get("limitations"):
                unknowns.append("当前索引使用有限关系解析，动态派发/反射仍需现场验证。")
            likely_modules = sorted(set(impact.get("affected_modules", []))) or sorted({self._module_from_path(item.get("path", "")) for item in symbol_matches if item.get("path")})
            retrieval_explanation = {
                "task_type": intent["task_type"], "signals": intent["signals"], "rationale": intent["rationale"],
                "selected_records": [{"id": item["id"], "score": item["score"], "why_selected": item.get("why_selected", "")} for item in selected_results],
                "reference_count": len(reference_implementations), "reference_implementations": [{"symbol": item.get("symbol", item.get("record")), "path": item.get("path")} for item in reference_implementations], "extension_point_count": len(extension_points), "extension_points": [{"symbol": item.get("symbol"), "path": item.get("path")} for item in extension_points], "unknown_count": len(unknowns), "unknowns": unknowns[:4],
                "impact": {"modules": impact.get("affected_modules", [])[:8], "files": impact.get("affected_files", [])[:8], "tests": impact.get("affected_tests", [])[:8], "relations": len(impact.get("relations", []))},
            }
            result = {
                "task": task, "project": self.config.project_name,
                "task_type": intent["task_type"], "likely_modules": likely_modules,
                "index": {"commit": status.get("index_commit"), "pending_files": status.get("pending_files", [])},
                "summary": self._context_summary(fragments, impact),
                "knowledge": fragments,
                "symbols": symbol_matches[:30],
                "impact": {key: impact.get(key, [])[:limit] for key, limit in [("affected_modules", 12), ("affected_files", 12), ("affected_tests", 8), ("affected_knowledge", 8)]},
                "retrieval_explanation": retrieval_explanation,
                "reference_implementations": reference_implementations,
                "extension_points": extension_points,
                "unknowns": unknowns,
                "verification_commands": verification,
                "gaps": gaps,
                "token_budget": budget,
                "estimated_tokens": 0,
                "guidance_workflow": status.get("guidance_workflow", {}),
            }
            self._fit_context(result, budget)
            self._record_query(store, "knowledge_context", len(task), result, started)
            return result

    @staticmethod
    def _guidance_workflow_status(store: KnowledgeStore) -> dict[str, Any]:
        guidance = GuidanceStore(store)
        row = store.connection.execute(
            "SELECT run_id FROM guidance_runs ORDER BY updated_at DESC, created_at DESC, run_id DESC LIMIT 1"
        ).fetchone()
        run = guidance.get_run(str(row["run_id"])) if row else None
        drafts = guidance.list_pending_drafts()
        changes = guidance.pending_changes()
        categories = guidance.list_categories()
        formal_guides = sum(1 for category in categories if guidance.current_version(category.category_id))
        formal_methodologies = sum(
            1 for category in categories
            if guidance.current_version(category.category_id, "methodology")
        )
        return {
            "available": run is not None,
            "run": run.to_dict() if run else None,
            "coverage": {
                "covered_files": run.covered_files if run else 0,
                "total_files": run.total_files if run else 0,
                "uncovered_files": list(run.uncovered_files) if run else [],
                "complete": bool(run and run.covered_files == run.total_files and not run.uncovered_files),
            },
            "categories": {
                "total": len(categories),
                "with_formal_guidance": formal_guides,
                "with_formal_methodology": formal_methodologies,
            },
            "pending_drafts": [
                {
                    "draft_id": draft.draft_id, "kind": draft.kind,
                    "category_id": draft.category_id, "status": draft.status,
                    "path": draft.path, "content_hash": draft.content_hash,
                }
                for draft in drafts
            ],
            "pending_changes": [
                {
                    "change_id": change.change_id, "level": change.update_level,
                    "changed_files": list(change.changed_files),
                    "affected_categories": list(change.affected_categories),
                    "base_snapshot_id": change.base_snapshot_id,
                    "head_snapshot_id": change.head_snapshot_id,
                }
                for change in changes
            ],
        }

    @staticmethod
    def _symbol_terms(task: str) -> list[str]:
        terms: list[str] = []

        def add(candidate: str) -> None:
            if len(candidate) >= 3 and candidate not in terms:
                terms.append(candidate)

        ascii_terms = re.findall(r"[A-Za-z_$][\w$.:/-]{2,}", task)
        structured = [term for term in ascii_terms if "_" in term or "." in term or "::" in term]
        plain = [term for term in ascii_terms if term not in structured]

        for term in structured:
            add(term)
            for part in re.split(r"[.:/-]+", term):
                add(part)

        lowered = task.lower()
        for phrase, tokens in IDENTIFIER_PHRASE_TOKENS:
            if phrase not in lowered:
                continue
            add("_".join(tokens))
            for token in tokens:
                add(token)

        for term in plain:
            add(term)
            for part in re.split(r"[.:/-]+", term):
                add(part)

        for term in re.findall(r"[\u4e00-\u9fff]{2,}", task):
            add(term)
        return terms[:24]

    @classmethod
    def _relevant_excerpt(cls, content: str, task: str, budget: int) -> str:
        if approx_tokens(content) <= budget:
            return content
        lines = content.splitlines()
        terms = cls._symbol_terms(task)
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", task.lower()):
            terms.extend(chunk[index:index + 2] for index in range(max(0, len(chunk) - 1)))
        terms = list(dict.fromkeys(term.lower() for term in terms if len(term) >= 2))
        ranked: list[tuple[int, int]] = []
        for index, line in enumerate(lines):
            lowered = line.lower()
            score = sum(1 for term in terms if term in lowered)
            if any(keyword in lowered for keyword in ("不变量", "必须", "不能", "不得", "回滚", "验证")):
                score += 10
            if score:
                ranked.append((score, index))
        if not ranked:
            return trim_to_tokens(content, budget)
        selected: set[int] = set()
        for _, index in sorted(ranked, key=lambda item: (-item[0], item[1])):
            candidate_lines = selected | set(range(max(0, index - 1), min(len(lines), index + 2)))
            candidate = "\n".join(lines[position] for position in sorted(candidate_lines))
            if approx_tokens(candidate) <= budget:
                selected = candidate_lines
            if approx_tokens("\n".join(lines[position] for position in sorted(selected))) >= budget:
                break
        if not selected:
            selected.add(ranked[0][1])
        excerpt = "\n".join(lines[position] for position in sorted(selected))
        return trim_to_tokens(excerpt, budget)

    @staticmethod
    def _pending_guidance_draft(store: KnowledgeStore, record: KnowledgeRecord) -> dict[str, str] | None:
        if record.kind != "development-guide" or not record.id.startswith("guide."):
            return None
        category_id = record.id.split(".", 1)[1]
        rows = store.rows(
            "SELECT draft_id, path FROM guidance_drafts "
            "WHERE category_id=? AND kind='guidance' "
            "AND status IN ('incomplete', 'awaiting_confirmation') "
            "ORDER BY updated_at DESC, draft_id DESC LIMIT 1",
            [category_id],
        )
        return rows[0] if rows else None

    @staticmethod
    def _summary(content: str, limit: int = 480) -> str:
        clean = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:limit] + ("..." if len(clean) > limit else "")

    @staticmethod
    def _score_breakdown(record: KnowledgeRecord, text_score: float, module: str | None) -> dict[str, float]:
        confidence = CONFIDENCE_WEIGHT.get(record.confidence, 0)
        freshness = FRESHNESS_WEIGHT.get(record.status, -1)
        kind_boost = 2.0 if record.kind in {"feature-guide", "development-guide"} else 0.0
        module_boost = 0.5 if module and module in record.tags else 0.0
        return {"text_match": round(text_score, 4), "confidence": confidence, "freshness": freshness, "kind_boost": kind_boost, "module_boost": module_boost, "total": round(text_score + confidence + freshness + kind_boost + module_boost, 4)}

    @staticmethod
    def _why_selected(record: KnowledgeRecord, breakdown: dict[str, float]) -> str:
        reasons = [f"文本匹配 {breakdown['text_match']:.2f}", f"可信度 {record.confidence}"]
        if record.kind in {"feature-guide", "development-guide"}: reasons.append("Feature Guide 优先")
        if record.status != "fresh": reasons.append(f"知识状态 {record.status}，需复核")
        return "；".join(reasons) + "。"

    @staticmethod
    def _impact_explanation(files: list[str], symbols: list[str], max_hops: int, relations: list[dict[str, Any]], modules: list[str], tests: list[str]) -> str:
        anchors = ", ".join(symbols[:4] or files[:4]) or "未提供锚点"
        return f"以 {anchors} 为起点，沿已解析关系最多追踪 {max_hops} 跳，得到 {len(relations)} 条关系；涉及模块 {', '.join(modules[:6]) or '未识别'}，建议验证测试 {', '.join(tests[:6]) or '未识别'}。"

    @staticmethod
    def _module_from_path(path: str) -> str:
        parts = Path(path).parts
        return parts[0] if parts else ""

    @classmethod
    def _reference_implementations(cls, symbol_matches: list[dict[str, Any]], selected_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        references = [{"symbol": item["id"], "name": item["name"], "path": item["path"], "line": item.get("line"), "kind": item["kind"], "reason": "任务词与符号名称精确或前缀命中。"} for item in symbol_matches[:4]]
        if not references: references = [{"record": item["id"], "path": item["path"], "kind": item["kind"], "reason": item.get("why_selected", "")} for item in selected_results[:4]]
        return references

    @staticmethod
    def _extension_points(symbol_matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keywords = ("create", "add", "extend", "register", "save", "use", "新增", "扩展", "注册")
        return [{"symbol": item["id"], "name": item["name"], "path": item["path"], "line": item.get("line"), "reason": "可作为新增功能的现有实现或扩展锚点。"} for item in symbol_matches if item.get("kind") in {"function", "method", "class"} or any(keyword in item.get("name", "").lower() for keyword in keywords)][:4]

    @staticmethod
    def _pending_sources(record: KnowledgeRecord, pending: set[str]) -> list[str]:
        return sorted({source.path for source in record.sources if source.path and source.path in pending})

    @staticmethod
    def _context_summary(fragments: list[dict[str, Any]], impact: dict[str, Any]) -> str:
        if not fragments:
            return "No reliable project knowledge matched; inspect live source using the returned gaps."
        modules = ", ".join(impact.get("affected_modules", [])[:8]) or "not statically identified"
        return f"Retrieved {len(fragments)} source-traceable knowledge records. Likely modules: {modules}."

    def _verification_commands(self) -> list[str]:
        candidates = [
            ("pyproject.toml", "python -m pytest"),
            ("pytest.ini", "python -m pytest"),
            ("package.json", "npm test"),
            ("Cargo.toml", "cargo test"),
            ("go.mod", "go test ./..."),
            ("pom.xml", "mvn test"),
            ("build.gradle", "gradle test"),
            ("Makefile", "make test"),
        ]
        commands: list[str] = []
        for path, command in candidates:
            if (self.root / path).exists() and command not in commands:
                commands.append(command)
        return commands

    @staticmethod
    def _fit_context(result: dict[str, Any], budget: int) -> None:
        result["symbols"] = result["symbols"][:8]
        for key, limit in [("affected_files", 8), ("affected_tests", 4), ("affected_knowledge", 4)]:
            result["impact"][key] = result["impact"][key][:limit]
        result["impact"]["affected_modules"] = result["impact"]["affected_modules"][:8]
        result["reference_implementations"] = result.get("reference_implementations", [])[:2]
        result["extension_points"] = result.get("extension_points", [])[:2]
        explanation = result.get("retrieval_explanation", {})
        explanation["selected_records"] = explanation.get("selected_records", [])[:2]
        explanation.get("impact", {}).update({"files": explanation.get("impact", {}).get("files", [])[:4], "tests": explanation.get("impact", {}).get("tests", [])[:4]})

        def size() -> int:
            result["estimated_tokens"] = 0
            measured = approx_tokens(json.dumps(result, ensure_ascii=False))
            result["estimated_tokens"] = measured
            return approx_tokens(json.dumps(result, ensure_ascii=False))

        for _ in range(200):
            if size() <= budget:
                return
            contents = [item for item in result["knowledge"] if item.get("tokens", 0) > 60]
            if contents:
                longest = max(contents, key=lambda item: len(item["content"]))
                longest["content"] = trim_to_tokens(longest["content"], max(40, longest["tokens"] // 2))
                longest["tokens"] = approx_tokens(longest["content"])
                continue
            if len(result["knowledge"]) > 1:
                result["knowledge"].pop()
                continue
            if result["symbols"]:
                result["symbols"].pop()
                continue
            impact_lists = [value for value in result["impact"].values() if isinstance(value, list) and value]
            if impact_lists:
                max(impact_lists, key=len).pop()
                continue
            if len(result["gaps"]) > 1:
                result["gaps"].pop()
                continue
            result["summary"] = trim_to_tokens(result["summary"], 30)
            break
        size()

    @staticmethod
    def _record_query(store: KnowledgeStore, tool: str, input_size: int, result: dict[str, Any], started: float) -> None:
        store.record_query(
            utc_now(), tool, input_size, approx_tokens(json.dumps(result, ensure_ascii=False)), int((time.monotonic() - started) * 1000)
        )
