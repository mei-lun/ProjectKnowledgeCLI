from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .guidance_store import GuidanceStore
from .models import CanonicalFile, CanonicalSymbol, KnowledgeRecord, RetrievalCandidate
from .ranking import (
    DEFAULT_RANKING_POLICY,
    LEGACY_RANKING_POLICY,
    FileCandidate,
    fallback_rank_files,
    rank_files,
)
from .service import ProjectService
from .store import SCHEMA_VERSION, KnowledgeStore
from .util import approx_tokens, project_lock, trim_to_tokens, utc_now
from .vector import VectorIndex


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


RECALL_CHANNEL_LIMITS = {
    "path_exact": 20,
    "symbol_exact": 50,
    "symbol_alias": 50,
    "lexical": 100,
    "knowledge": 30,
    "graph_direct": 100,
    "graph_multihop": 100,
    "test_config": 50,
}

# Deterministic, reviewable seed aliases. Project-specific identifiers still come
# from the live CodeGraph symbol index; aliases only add queries and never facts.
QUERY_ALIAS_GROUPS = (
    (("物品", "道具", "item"), ("item", "create_item", "add_item")),
    (("登录", "认证", "login"), ("AccountApi.login", "AccountComponent.do_login", "account_component", "account_api", "do_login", "login", "account", "authenticate")),
    (("角色", "role"), ("do_role_create", "role_create", "avatar", "role")),
    (("生命周期", "lifecycle"), ("avatar/base.lua", "on_login_handler", "avatar_def")),
    (("花园", "garden"), ("garden_com", "garden_sys", "garden", "farm")),
    (("种植", "培育", "cultivation"), ("start_cultivation", "claim_cultivation", "cultivation", "farm")),
    (("订单", "order"), ("resident_order", "customer_order", "order")),
    (("居民订单", "常驻订单", "resident order"), ("get_order_strict", "set_order", "ResidentOrderCom", "resident_order")),
    (("顾客订单", "顾客首单", "首单", "customer order"), ("mark_first_order_generated", "CustomerOrderCom", "customer_order")),
    (("组件注册", "注册到", "在哪里注册", "注册表", "component registry"), ("avatar_def", "registry", "register")),
    (("配置", "configuration", "config"), ("tblconf", "config", "conf")),
    (("测试", "test"), ("src_test", "src_dev", "unittest", "test")),
)

GENERIC_SYMBOL_NAMES = {
    "add", "call", "check", "close", "create", "delete", "get", "handle",
    "init", "load", "main", "new", "open", "process", "read", "run", "save",
    "send", "set", "start", "stop", "test", "update", "write",
}
VENDOR_PATH_PREFIXES = ("modules/", "vendor/", "third_party/", "deps/", "external/")
GENERATED_PATH_MARKERS = ("/generated/", "/dist/", "/build/", ".generated.")


class KnowledgeAPI:
    def __init__(self, project: str | Path = "."):
        self.service = ProjectService(project)
        self.root = self.service.root
        self.config = ProjectConfig.load(self.root)
        if not self.service.db_path.exists():
            raise RuntimeError(f"{self.root} is not initialized")
        self._ensure_current_schema()

    def _ensure_current_schema(self) -> None:
        with KnowledgeStore(self.service.db_path, readonly=True) as store:
            if store.get_meta("schema_version") == str(SCHEMA_VERSION):
                return
        with project_lock(self.root), KnowledgeStore(self.service.db_path) as store:
            store.initialize()

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

    def search(
        self,
        query: str,
        kinds: list[str] | None = None,
        module: str | None = None,
        limit: int = 10,
        debug: bool = False,
    ) -> dict[str, Any]:
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
            vector_matches, vector_diagnostics = VectorIndex(store, self.config).search(query, limit=limit * 2)
            vector_scores = {item.record_id: item.similarity for item in vector_matches}
            lexical_ids = {record.id for record, _ in matches}
            for vector_match in vector_matches:
                if vector_match.record_id in seen_ids:
                    continue
                record = store.get_knowledge(vector_match.record_id)
                if record is None or (kinds and record.kind not in kinds):
                    continue
                if module and module not in record.tags and f"/{module}/" not in record.path:
                    continue
                matches.append((record, 0.0))
                seen_ids.add(record.id)
            ranked: list[tuple[KnowledgeRecord, float, float, float, bool]] = []
            for record, text_score in matches:
                score = text_score + CONFIDENCE_WEIGHT.get(record.confidence, 0) + FRESHNESS_WEIGHT.get(record.status, -1)
                if module and module in record.tags:
                    score += 0.5
                if record.kind in {"feature-guide", "development-guide"}:
                    score += 2.0
                vector_similarity = vector_scores.get(record.id, 0.0)
                vector_boost = min(0.25, max(0.0, vector_similarity) * 0.25)
                if record.id in lexical_ids:
                    score += vector_boost
                else:
                    score = -1.0 + vector_boost
                ranked.append((record, score, text_score, vector_similarity, record.id in lexical_ids))
            ranked.sort(key=lambda item: (not item[4], -item[1], item[0].id))
            items = []
            for record, score, text_score, vector_similarity, _ in ranked[:limit]:
                pending_sources = self._pending_sources(record, pending)
                summary = (
                    f"[withheld: depends on pending source {', '.join(pending_sources)}]"
                    if pending_sources else self._summary(record.content)
                )
                breakdown = self._score_breakdown(record, text_score, module, vector_similarity)
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
            result = {
                "query": query,
                "results": items,
                "gaps": [] if items else ["No matching knowledge record; search live source."],
                "vector_retrieval": vector_diagnostics,
            }
            if debug:
                result["retrieval_trace"] = {
                    "schema_version": 1,
                    "operation": "knowledge_search",
                    "query": {"raw": query, "terms": self._symbol_terms(query)},
                    "channels": {
                        "lexical": len(lexical_ids),
                        "vector": len(vector_matches),
                    },
                    "candidate_count": len(ranked),
                    "deduplicated_count": len(seen_ids),
                    "returned_count": len(items),
                    "ranking": [
                        {
                            "candidate_id": item["id"],
                            "score": item["score"],
                            "score_breakdown": item["score_breakdown"],
                        }
                        for item in items
                    ],
                }
            self._record_query(store, "knowledge_search", len(query), result, started)
            return result

    def impact(
        self,
        files: list[str] | None = None,
        symbols: list[str] | None = None,
        max_hops: int = 1,
        max_relations: int = 500,
        debug: bool = False,
    ) -> dict[str, Any]:
        started = time.monotonic()
        files = [Path(path).as_posix().lstrip("./") for path in (files or [])]
        symbols = symbols or []
        max_hops = max(0, min(int(max_hops), 5))
        max_relations = max(1, min(int(max_relations), 5000))
        engine_result = dict(self.service.engine.impact(
            self.root,
            self.service.config,
            files=files,
            symbols=symbols,
            max_hops=max_hops,
            max_relations=max_relations,
        ))
        affected_files = set(engine_result.get("affected_files", []))
        affected_symbols = set(engine_result.get("affected_symbols", []))
        with KnowledgeStore(self.service.db_path) as store:
            knowledge: list[dict[str, Any]] = []
            for record in store.all_knowledge():
                source_keys = {source.path or source.id for source in record.sources}
                if source_keys.intersection(affected_files | affected_symbols):
                    knowledge.append({
                        "id": record.id,
                        "title": record.title,
                        "path": record.path,
                        "freshness": record.status,
                        "confidence": record.confidence,
                    })
            engine_result.update({
                "input": {"files": files, "symbols": symbols},
                "max_hops": max_hops,
                "max_relations": max_relations,
                "affected_knowledge": knowledge,
                "impact_explanation": self._impact_explanation(
                    files,
                    symbols,
                    max_hops,
                    list(engine_result.get("relations", [])),
                    list(engine_result.get("affected_modules", [])),
                    list(engine_result.get("affected_tests", [])),
                ),
                "truncated": len(engine_result.get("relations", [])) >= max_relations,
                "fact_source": "codegraph",
                "dependency_files": sorted(affected_files),
                "limitations": self.service.engine.status().get("limitations", []),
            })
            if debug:
                engine_result["retrieval_trace"] = {
                    "schema_version": 1,
                    "operation": "knowledge_impact",
                    "anchors": {"files": files, "symbols": symbols},
                    "max_hops": max_hops,
                    "max_relations": max_relations,
                    "relation_count": len(engine_result.get("relations", [])),
                    "affected_file_count": len(engine_result.get("affected_files", [])),
                    "affected_symbol_count": len(engine_result.get("affected_symbols", [])),
                    "affected_knowledge_count": len(knowledge),
                    "truncated": engine_result["truncated"],
                    "fact_source": "codegraph",
                }
            self._record_query(store, "knowledge_impact", len(json.dumps(engine_result["input"])), engine_result, started)
        return engine_result

    def context(
        self,
        task: str,
        max_tokens: int | None = None,
        debug: bool = False,
    ) -> dict[str, Any]:
        started = time.monotonic()
        budget = max(256, min(max_tokens or self.config.max_tokens, 50_000))
        status = self.status()
        intent = self.classify_task(task)
        search = self.search(task, limit=10, debug=debug)
        broad_project_requested = any(
            phrase in task.lower()
            for phrase in ["project map", "repository overview", "project overview", "项目地图", "项目概览"]
        )
        selected_results = [
            item for item in search["results"]
            if broad_project_requested or item["kind"] != "project"
        ][:4]
        terms = self._symbol_terms(task)
        symbol_matches = self._task_symbol_matches(task, terms)
        with KnowledgeStore(self.service.db_path) as store:
            impact = self.impact(
                symbols=[item["id"] for item in symbol_matches[:10]],
                max_hops=2,
                max_relations=200,
            ) if symbol_matches else {
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
                "vector_retrieval": search.get("vector_retrieval", {}),
            }
            result = {
                "task": task, "project": self.config.project_name,
                "task_type": intent["task_type"], "likely_modules": likely_modules,
                "index": {"commit": status.get("index_commit"), "pending_files": status.get("pending_files", [])},
                "summary": self._context_summary(fragments, impact),
                "knowledge": fragments,
                "symbols": [
                    {
                        key: value
                        for key, value in item.items()
                        if key not in {"symbol_score", "symbol_score_breakdown"}
                    }
                    for item in symbol_matches[:30]
                ],
                "impact": {
                    **{
                        key: impact.get(key, [])[:limit]
                        for key, limit in [
                            ("affected_modules", 12),
                            ("affected_files", 12),
                            ("dependency_files", 12),
                            ("affected_tests", 8),
                            ("affected_knowledge", 8),
                        ]
                    },
                    "call_path": list(dict.fromkeys(
                        endpoint
                        for relation in impact.get("relations", [])
                        for endpoint in (relation.get("source"), relation.get("target"))
                        if endpoint and (relation.get("resolved") or endpoint == relation.get("source"))
                    ))[:30],
                },
                "retrieval_explanation": retrieval_explanation,
                "reference_implementations": reference_implementations,
                "extension_points": extension_points,
                "unknowns": unknowns,
                "verification_commands": verification,
                "gaps": gaps,
                "token_budget": budget,
                "estimated_tokens": 0,
                "guidance_workflow": status.get("guidance_workflow", {}),
                "vector_retrieval": search.get("vector_retrieval", {}),
                "fact_source": "codegraph",
                "engine_limitations": self.service.engine.status().get("limitations", []),
            }
            candidates, allowed_paths = self._context_file_candidates(
                task, intent, symbol_matches, impact, fragments
            )
            query_profile = self._query_profile(task)
            ranking_policy = (
                LEGACY_RANKING_POLICY
                if self.config.ranking_policy == "policy-v1"
                else DEFAULT_RANKING_POLICY
            )
            try:
                ranked_files = rank_files(
                    candidates,
                    allowed_paths=allowed_paths,
                    policy=ranking_policy,
                    query_type=query_profile,
                )
            except Exception:
                ranked_files = fallback_rank_files(
                    candidates,
                    allowed_paths=allowed_paths,
                    reason_code="ranking_error",
                    policy=ranking_policy,
                    query_type=query_profile,
                )
            result.update(ranked_files.to_dict())
            result["query_profile"] = query_profile
            result["ranking_reason_code"] = ranked_files.reason_code
            prefit_files = list(result["files"])
            prefit_core_files = list(result["core_files"])
            self._fit_context(result, budget)
            if debug:
                result["retrieval_trace"] = self._context_retrieval_trace(
                    task=task,
                    intent=intent,
                    status=status,
                    search_trace=search.get("retrieval_trace", {}),
                    symbol_matches=symbol_matches,
                    candidates=candidates,
                    allowed_paths=allowed_paths,
                    ranked_files=ranked_files.to_dict(),
                    prefit_files=prefit_files,
                    prefit_core_files=prefit_core_files,
                    result=result,
                    budget=budget,
                )
            self._record_query(store, "knowledge_context", len(task), result, started)
            return result

    def _context_retrieval_trace(
        self,
        *,
        task: str,
        intent: dict[str, Any],
        status: dict[str, Any],
        search_trace: dict[str, Any],
        symbol_matches: list[dict[str, Any]],
        candidates: list[FileCandidate],
        allowed_paths: set[str],
        ranked_files: dict[str, Any],
        prefit_files: list[str],
        prefit_core_files: list[str],
        result: dict[str, Any],
        budget: int,
    ) -> dict[str, Any]:
        snapshot = self.service.engine.snapshot(self.root, self.service.config)
        snapshot_files = {item.path.replace("\\", "/"): item for item in snapshot.files}
        repository_id = self.config.project_name or self.root.name
        source_revision = str(status.get("head_commit") or snapshot.snapshot_id)
        pending = {str(path).replace("\\", "/") for path in status.get("pending_files", [])}

        canonical_files: dict[str, CanonicalFile] = {}
        for path in sorted(allowed_paths):
            indexed = snapshot_files.get(path)
            if indexed is None:
                continue
            lowered = path.lower()
            canonical_files[path] = CanonicalFile(
                repository_id=repository_id,
                commit=source_revision,
                path=path,
                language=indexed.language,
                module=indexed.module,
                file_hash=indexed.content_hash,
                status="potentially_stale" if path in pending else "fresh",
                metadata={
                    "is_test": "test" in Path(path).name.lower() or "/test" in lowered,
                    "is_generated": "/generated/" in f"/{lowered}/",
                    "is_vendor": lowered.startswith(VENDOR_PATH_PREFIXES)
                    or any(part in lowered.split("/") for part in ("vendor", "node_modules")),
                },
            )

        canonical_symbols = [
            self._canonical_symbol(item, repository_id, source_revision, canonical_files)
            for item in symbol_matches
            if str(item.get("path", "")).replace("\\", "/") in canonical_files
        ]
        trace_candidates = self._canonical_trace_candidates(candidates, canonical_files)
        channel_counts: dict[str, int] = {}
        for candidate in trace_candidates:
            for channel in candidate.channels:
                channel_counts[channel] = channel_counts.get(channel, 0) + 1
        unique_paths = {candidate.file for candidate in trace_candidates}
        final_files = list(result.get("files", []))
        final_core = list(result.get("core_files", []))
        token_withheld = [
            item for item in result.get("withheld_files", [])
            if item.get("reason_code") == "token_budget"
        ]
        return {
            "schema_version": 1,
            "operation": "knowledge_context",
            "query": {
                "raw": task,
                "intent": intent.get("task_type", "investigation"),
                "profile": self._query_profile(task),
                "entities": self._symbol_terms(task),
                "aliases": self._query_aliases(task, self._symbol_terms(task)),
                "relations": self._intent_relations(str(intent.get("task_type", "investigation"))),
                "constraints": {"freshness": "exclude_pending"},
            },
            "source": {
                "repository_id": repository_id,
                "snapshot_id": snapshot.snapshot_id,
                "revision": source_revision,
                "fact_source": "codegraph",
                "file_count": len(snapshot.files),
            },
            "stages": {
                "knowledge_recall": search_trace,
                "symbol_recall": {
                    "candidate_count": len(canonical_symbols),
                    "candidates": [item.to_dict() for item in canonical_symbols[:50]],
                    "ranking": [
                        {
                            "symbol_id": str(item.get("id", "")),
                            "score": int(item.get("symbol_score", 0)),
                            "score_breakdown": item.get("symbol_score_breakdown", {}),
                            "matched_term": str(item.get("matched_term", "")),
                            "channel": str(item.get("recall_channel", "")),
                        }
                        for item in symbol_matches[:50]
                    ],
                },
                "file_recall": {
                    "candidate_count": len(candidates),
                    "channel_counts": channel_counts,
                    "candidates": [item.to_dict() for item in trace_candidates[:100]],
                },
                "canonical_dedup": {
                    "input_count": len(candidates),
                    "unique_count": len(unique_paths),
                    "duplicates_removed": max(0, len(candidates) - len(unique_paths)),
                    "pending_filtered": len(pending.intersection(allowed_paths)),
                },
                "ranking": {
                    "policy": ranked_files.get("ranking_policy"),
                    "status": ranked_files.get("ranking_status"),
                    "confidence": ranked_files.get("ranking_confidence"),
                    "candidates": ranked_files.get("file_rankings", []),
                    "withheld": ranked_files.get("withheld_files", []),
                    "rejected": ranked_files.get("rejected_files", []),
                },
                "context_assembly": {
                    "core_files": final_core,
                    "supporting_files": list(result.get("supporting_files", [])),
                    "selected_before_budget": prefit_files,
                },
                "token_budget": {
                    "budget": budget,
                    "estimated_tokens_without_trace": result.get("estimated_tokens", 0),
                    "withheld": token_withheld,
                    "context_incomplete": not set(prefit_core_files).issubset(final_core),
                },
            },
        }

    @staticmethod
    def _intent_relations(intent: str) -> list[str]:
        return {
            "new_feature": ["implements", "calls", "tests", "configures"],
            "bug_fix": ["callers", "callees", "tests"],
            "refactor": ["callers", "callees", "imports", "tests"],
            "impact_analysis": ["callers", "callees", "implements", "tests"],
        }.get(intent, ["defines", "calls"])

    @staticmethod
    def _canonical_symbol(
        item: dict[str, Any],
        repository_id: str,
        source_revision: str,
        files: dict[str, CanonicalFile],
    ) -> CanonicalSymbol:
        path = str(item.get("path", "")).replace("\\", "/")
        public_id = str(item.get("id", ""))
        qualified = public_id.split("::", 1)[1] if "::" in public_id else str(item.get("name", ""))
        parts = qualified.split("::")
        line = max(1, int(item.get("line") or 1))
        end_line = max(line, int(item.get("end_line") or line))
        file = files[path]
        return CanonicalSymbol(
            symbol_id=f"repo://{repository_id}/{source_revision}/{path}#{qualified}@{line}",
            qualified_name=qualified,
            short_name=str(item.get("name", parts[-1] if parts else qualified)),
            kind=str(item.get("kind", "unknown")),
            path=path,
            signature=str(item.get("signature", "")),
            span={"start": line, "end": end_line},
            parent="::".join(parts[:-1]),
            aliases=(),
            source_commit=source_revision,
            source_hash=file.file_hash,
            freshness=file.status,
        )

    @staticmethod
    def _canonical_trace_candidates(
        candidates: list[FileCandidate],
        files: dict[str, CanonicalFile],
    ) -> list[RetrievalCandidate]:
        grouped: dict[str, list[FileCandidate]] = {}
        for candidate in candidates:
            path = candidate.path.replace("\\", "/")
            if path in files:
                grouped.setdefault(path, []).append(candidate)
        result: list[RetrievalCandidate] = []
        channel_names = {
            "direct_symbol": "symbol_exact",
            "knowledge_source": "knowledge",
            "impact": "graph_direct",
            "fallback": "lexical",
        }
        for path, rows in grouped.items():
            file = files[path]
            channels = {channel for row in rows for channel in row.channels}
            channels.update({
                channel_names.get(stage, stage)
                for row in rows
                for stage in row.stages
                if stage in channel_names
            })
            if any(row.graph_hop == 2 for row in rows):
                channels.add("graph_multihop")
            anchors = sorted({anchor for row in rows for anchor in row.anchors})
            features: dict[str, float | int | bool | str] = {
                "exact_symbol": any(row.exact_symbol for row in rows),
                "exact_path": any(row.exact_path for row in rows),
                "relation_hop": min(
                    (row.graph_hop for row in rows if row.graph_hop is not None),
                    default=0,
                ),
                "path_term_count": len({term for row in rows for term in row.path_terms}),
                "symbol_term_count": len({term for row in rows for term in row.symbol_terms}),
                "is_test": file.metadata.get("is_test", False),
                "freshness": file.status,
            }
            evidence = [f"file:{path}", f"hash:{file.file_hash}"]
            evidence.extend(f"anchor:{anchor}" for anchor in anchors[:8])
            edge_type = (
                "defines" if "direct_symbol" in {stage for row in rows for stage in row.stages}
                else "documents" if "knowledge_source" in {stage for row in rows for stage in row.stages}
                else "depends_on" if "impact" in {stage for row in rows for stage in row.stages}
                else "lexical_match"
            )
            result.append(RetrievalCandidate(
                candidate_id=file.file_id,
                file=path,
                channels=tuple(sorted(channels)),
                graph_paths=tuple(
                    {"from": anchor, "edge": edge_type, "to": file.file_id}
                    for anchor in anchors[:8]
                ),
                features=features,
                evidence=tuple(evidence),
                stage="canonicalized",
            ))
        return result

    def _context_file_candidates(
        self,
        task: str,
        intent: dict[str, Any],
        symbol_matches: list[dict[str, Any]],
        impact: dict[str, Any],
        fragments: list[dict[str, Any]],
    ) -> tuple[list[FileCandidate], set[str]]:
        def normalized(path: object) -> str:
            return str(path).replace("\\", "/").lstrip("./")

        snapshot = self.service.engine.snapshot(self.root, self.service.config)
        allowed_paths = {normalized(item.path) for item in snapshot.files if normalized(item.path)}
        modules = {normalized(item.path): item.module for item in snapshot.files}
        pending_paths = {
            normalized(path)
            for path in self.service.status().get("pending_files", [])
        }
        original_terms = {term.lower() for term in self._symbol_terms(task)}
        alias_terms = {
            term.lower() for term in self._query_aliases(task, list(original_terms))
        }
        terms = original_terms | alias_terms
        query_profile = self._query_profile(task)
        engine_status = self.service.engine.status()
        capabilities = set(engine_status.get("capabilities", []))
        unavailable_signals = {"graph"} if capabilities and "impact" not in capabilities else set()
        relation_hops: dict[str, int] = {}
        relation_degrees: dict[str, int] = {}
        endpoint_hops: dict[str, int] = {}
        for relation in impact.get("relations", []):
            path = normalized(relation.get("path", ""))
            hop = relation.get("hop", 1)
            if path and isinstance(hop, int):
                relation_hops[path] = min(relation_hops.get(path, hop), hop)
            if isinstance(hop, int):
                for endpoint in (relation.get("source"), relation.get("target")):
                    if endpoint:
                        endpoint_id = str(endpoint)
                        endpoint_hops[endpoint_id] = min(
                            endpoint_hops.get(endpoint_id, hop), hop
                        )
                        # Public CodeGraph relation endpoints commonly use
                        # ``path::symbol`` identifiers. Resolve that path
                        # directly without consulting the removed SQLite symbol cache.
                        endpoint_path = normalized(endpoint_id.split("::", 1)[0])
                        if endpoint_path in allowed_paths:
                            relation_degrees[endpoint_path] = relation_degrees.get(endpoint_path, 0) + 1
                            relation_hops[endpoint_path] = min(
                                relation_hops.get(endpoint_path, hop), hop
                            )
        for item in symbol_matches:
            symbol_id = str(item.get("id", ""))
            path = normalized(item.get("path", ""))
            if symbol_id in endpoint_hops and path:
                hop = endpoint_hops[symbol_id]
                relation_hops[path] = min(relation_hops.get(path, hop), hop)

        candidates: list[FileCandidate] = []
        task_explicitly_requests_tests = any(
            term in task.lower() for term in ("test", "pytest", "unittest", "测试")
        )

        def matching_terms(value: str) -> set[str]:
            lowered = value.lower()
            return {term for term in terms if term in lowered}

        test_query_terms = {"test", "src_test", "src_dev", "unittest", "spec"}

        def business_matching_terms(value: str) -> set[str]:
            return matching_terms(value) - test_query_terms

        def identity(path: str, symbol: str = "") -> dict[str, bool]:
            lowered_path = path.lower()
            filename = Path(path).name.lower()
            stem = Path(path).stem.lower()
            module = modules.get(path, self._module_from_path(path)).lower()
            lowered_symbol = symbol.lower()
            symbol_name = lowered_symbol.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
            return {
                "exact_symbol": bool(symbol and any(term in {lowered_symbol, symbol_name} for term in original_terms)),
                "qualified_symbol": bool(symbol and any(lowered_symbol.startswith(term) for term in original_terms)),
                "exact_path": lowered_path in original_terms,
                "exact_filename": filename in original_terms or stem in original_terms,
                "exact_module": module in original_terms,
            }

        def add(
            path: object,
            *,
            stage: str,
            channel: str,
            anchor: str = "",
            symbol: str = "",
            graph_hop: int | None = None,
            affected_test: bool = False,
            direct_knowledge_source: bool = False,
            content: str = "",
            symbol_kind: str = "",
            matched_term: str = "",
            knowledge_confidence: str = "",
        ) -> None:
            normalized_path = normalized(path)
            if not normalized_path or normalized_path in pending_paths:
                return
            identities = identity(normalized_path, symbol)
            is_test = "test" in Path(normalized_path).name.lower() or "test" in normalized_path.lower()
            if is_test and not task_explicitly_requests_tests:
                identities["exact_symbol"] = False
                identities["qualified_symbol"] = False
            lowered_matched_term = matched_term.lower()
            lowered_symbol_name = symbol.rsplit("::", 1)[-1].rsplit(".", 1)[-1].lower()
            generic_symbol = bool(
                identities["exact_symbol"]
                and (
                    lowered_symbol_name in GENERIC_SYMBOL_NAMES
                    or lowered_matched_term in GENERIC_SYMBOL_NAMES
                )
            )
            specific_symbol = bool(
                channel in {"symbol_exact", "symbol_alias"}
                and lowered_matched_term
                and lowered_matched_term not in GENERIC_SYMBOL_NAMES
                and ("_" in lowered_matched_term or len(lowered_matched_term) >= 8)
            )
            query_role_match = self._query_role_match(query_profile, normalized_path, symbol)
            if query_profile == "test_config" and is_test:
                query_role_match = bool(
                    business_matching_terms(normalized_path)
                    or business_matching_terms(symbol)
                )
            candidates.append(FileCandidate(
                path=normalized_path,
                stages={stage},
                channels={channel},
                anchors={anchor} if anchor else set(),
                module=modules.get(normalized_path, self._module_from_path(normalized_path)),
                path_terms=matching_terms(normalized_path),
                symbol_terms=matching_terms(symbol),
                content_terms=matching_terms(content),
                graph_hop=graph_hop,
                affected_test=affected_test,
                direct_knowledge_source=direct_knowledge_source,
                verified_knowledge=(knowledge_confidence == "verified"),
                is_test=is_test,
                task_role_match=bool(intent.get("task_type") != "investigation" and matching_terms(normalized_path)),
                query_role_match=query_role_match,
                definition_match=bool(
                    channel in {"symbol_exact", "symbol_alias"}
                    and symbol_kind in {"class", "function", "method", "file"}
                ),
                specific_symbol=specific_symbol,
                generic_symbol=generic_symbol,
                is_vendor=normalized_path.lower().startswith(VENDOR_PATH_PREFIXES),
                is_generated=any(marker in f"/{normalized_path.lower()}" for marker in GENERATED_PATH_MARKERS),
                high_degree_hub=relation_degrees.get(normalized_path, 0) >= 12,
                auxiliary_source=(
                    "/robot/" in f"/{normalized_path.lower()}"
                    and "robot" not in task.lower()
                ),
                unavailable_signals=set(unavailable_signals),
                original_order=len(candidates),
                **identities,
            ))

        for match in symbol_matches:
            symbol_id = str(match.get("id", match.get("name", "")))
            symbol_name = str(match.get("name", ""))
            add(
                match.get("path", ""),
                stage="direct_symbol",
                channel=str(match.get("recall_channel", "symbol_exact")),
                anchor=symbol_id,
                symbol=f"{symbol_id}::{symbol_name}",
                symbol_kind=str(match.get("kind", "")),
                matched_term=str(match.get("matched_term", "")),
                graph_hop=relation_hops.get(normalized(match.get("path", ""))),
            )
        for key in ("dependency_files", "affected_files"):
            for path in impact.get(key, []):
                normalized_path = normalized(path)
                hop = relation_hops.get(normalized_path, 1)
                add(
                    path,
                    stage="impact",
                    channel="graph_direct" if hop <= 1 else "graph_multihop",
                    graph_hop=hop,
                )
        task_requests_tests = (
            intent.get("task_type") in {"new_feature", "bug_fix", "impact_analysis"}
            or any(term in task.lower() for term in ("test", "pytest", "unittest", "测试"))
        )
        affected_tests = sorted(
            {normalized(path) for path in impact.get("affected_tests", []) if normalized(path)},
            key=lambda path: (
                -len(matching_terms(path)),
                relation_hops.get(path, 99),
                path,
            ),
        )[: 4 if task_requests_tests else 2]
        for path in affected_tests:
            add(
                path,
                stage="impact",
                channel="test_config",
                graph_hop=1,
                affected_test=True,
            )
        for fragment in fragments:
            if fragment.get("freshness") != "fresh" or fragment.get("requires_live_source"):
                continue
            fragment_content = str(fragment.get("content", ""))
            lowered_content = fragment_content.lower()
            for source in fragment.get("sources", []):
                if source.get("path"):
                    source_path = normalized(source["path"])
                    source_id = str(source.get("id", ""))
                    add(
                        source_path,
                        stage="knowledge_source",
                        channel="knowledge",
                        anchor=str(fragment.get("id", "")),
                        direct_knowledge_source=(
                            source_path.lower() in lowered_content
                            or bool(source_id and source_id.lower() in lowered_content)
                        ),
                        knowledge_confidence=str(fragment.get("confidence", "")),
                        content=fragment_content,
                    )
        for path in sorted(allowed_paths):
            lowered_path = path.lower()
            filename = Path(path).name.lower()
            stem = Path(path).stem.lower()
            module = modules.get(path, self._module_from_path(path)).lower()
            if any(
                term in {lowered_path, filename, stem, module}
                for term in original_terms
            ):
                add(path, stage="fallback", channel="path_exact")
            if matching_terms(path):
                add(path, stage="fallback", channel="lexical")
            if (
                any(marker in lowered_path for marker in ("/config/", "/conf/", "test", "spec"))
                and matching_terms(path)
            ):
                add(path, stage="impact", channel="test_config")
        return self._limit_recall_candidates(candidates, RECALL_CHANNEL_LIMITS), allowed_paths

    @staticmethod
    def _limit_recall_candidates(
        candidates: list[FileCandidate],
        limits: dict[str, int],
    ) -> list[FileCandidate]:
        """Apply independent per-channel caps before canonical path deduplication."""
        counts: dict[str, int] = {}
        limited: list[FileCandidate] = []
        for candidate in candidates:
            channels = candidate.channels or {"lexical"}
            accepted = {
                channel
                for channel in channels
                if counts.get(channel, 0) < max(0, limits.get(channel, 0))
            }
            if not accepted:
                continue
            candidate.channels = accepted
            limited.append(candidate)
            for channel in accepted:
                counts[channel] = counts.get(channel, 0) + 1
        return limited

    def _task_symbol_matches(self, task: str, terms: list[str]) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        seen: set[str] = set()
        structured = [
            term for term in terms
            if any(marker in term for marker in ("_", ".", "::", "/"))
        ]
        plain = [term for term in terms if term not in structured]
        aliases = self._query_aliases(task, terms)
        query_profile = self._query_profile(task)
        aliases.sort(
            key=lambda term: (
                not self._alias_matches_profile(query_profile, term),
                not any(marker in term for marker in ("_", ".", "::", "/")),
                -len(term),
                term,
            )
        )
        query_terms = [(term, "symbol_exact") for term in structured[:8]]
        query_terms.extend((term, "symbol_alias") for term in aliases[:12])
        query_terms.extend((term, "symbol_exact") for term in plain[:8])
        channel_counts = {"symbol_exact": 0, "symbol_alias": 0}
        for term, channel in query_terms:
            if channel_counts[channel] >= RECALL_CHANNEL_LIMITS[channel]:
                continue
            symbol_limit = 8 if any(marker in term for marker in ("_", ".", "::", "/")) else 3
            for symbol in self.service.engine.search_symbols(
                self.root, self.service.config, term, limit=symbol_limit
            ):
                item = asdict(symbol)
                if item["id"] not in seen:
                    seen.add(item["id"])
                    item["recall_channel"] = channel
                    item["matched_term"] = term
                    item["symbol_score"], item["symbol_score_breakdown"] = self._symbol_match_score(
                        task, item
                    )
                    matches.append(item)
                    channel_counts[channel] += 1
        matches.sort(
            key=lambda item: (
                -int(item.get("symbol_score", 0)),
                str(item.get("path", "")),
                str(item.get("id", "")),
            )
        )
        return matches[:24]

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

    @staticmethod
    def _query_aliases(task: str, original_terms: list[str] | None = None) -> list[str]:
        """Return deterministic query expansions while preserving original terms."""
        lowered = task.lower()
        original = {term.lower() for term in (original_terms or [])}
        aliases: list[str] = []
        for triggers, expansions in QUERY_ALIAS_GROUPS:
            if not any(trigger.lower() in lowered for trigger in triggers):
                continue
            for expansion in expansions:
                if expansion.lower() not in original and expansion not in aliases:
                    aliases.append(expansion)
                elif expansion not in aliases:
                    # Keep explicit business identifiers visible in trace provenance.
                    aliases.append(expansion)
        return aliases[:24]

    @staticmethod
    def _query_profile(task: str) -> str:
        """Infer a retrieval profile separately from development-task intent."""
        lowered = task.lower()
        profiles = (
            ("call_path", ("调用链", "调用路径", "登录后", "生命周期", "从哪里进入", "call path", "call chain", "lifecycle")),
            ("impact", ("影响哪些", "影响范围", "调用方", "依赖方", "依赖", "测试范围", "impact", "affected", "callers", "dependency", "scope")),
            ("extension_point", ("扩展点", "注册到", "注册表", "在哪里注册", "registry", "register", "extension point", "factory", "plugin")),
            ("invariant", ("不变量", "严格读取", "缺失", "首单", "状态", "检查", "记录", "strict read", "invariant", "missing", "state", "status", "save")),
            ("design_reason", ("为什么", "设计原因", "决策", "adr", "design reason", "rationale")),
            ("configuration", ("配置", "默认值", "环境变量", "configuration", "config", "default value", "environment variable")),
            ("test_config", ("测试", "用例", "test", "unittest", "spec")),
        )
        for profile, signals in profiles:
            if any(signal in lowered for signal in signals):
                return profile
        return "workflow"

    @staticmethod
    def _alias_matches_profile(query_profile: str, alias: str) -> bool:
        lowered = alias.lower()
        markers = {
            "invariant": ("strict", "mark_", "set_", "get_", "validate", "assert"),
            "extension_point": ("_def", "registry", "register", "factory", "plugin"),
            "configuration": ("config", "conf", "tblconf", "env"),
            "call_path": ("api", "component", "router", "handler"),
            "impact": ("api", "component", "system", "_sys", "_com"),
            "test_config": ("test", "unittest", "spec", "config"),
            "workflow": ("api", "component", "router", "login", "handler"),
        }
        return any(marker in lowered for marker in markers.get(query_profile, ()))

    @staticmethod
    def _query_role_match(query_profile: str, path: str, symbol: str = "") -> bool:
        lowered = f"{path}::{symbol}".lower()
        markers = {
            "invariant": ("/com/", "_com.", "strict", "validate", "assert", "check", "mark_", "set_", "get_"),
            "extension_point": ("_def.", "/registry", "register", "registry", "factory", "plugin", "/routes/", "router", "/com/", "_com.", "/system/", "_sys."),
            "configuration": ("/config/", "/conf/", "config", "tblconf", ".env"),
            "call_path": ("/api/", "_api.", "component", "router", "/system/", "_sys.", "/avatar/", "avatar/base", "on_login_handler"),
            "impact": ("/api/", "component", "/system/", "_sys.", "/com/", "_com."),
            "test_config": ("test", "unittest", "/config/", "/conf/", "spec", "/api/", "_api.", "component", "/com/", "_com.", "/system/", "_sys."),
            "design_reason": ("/docs/", "/decisions/", "adr", "architecture"),
            "workflow": ("/api/", "_api.", "component", "router", "/system/", "_sys.", "/com/", "_com."),
        }
        return any(marker in lowered for marker in markers.get(query_profile, ()))

    @classmethod
    def _symbol_match_score(
        cls,
        task: str,
        item: dict[str, Any],
    ) -> tuple[int, dict[str, int]]:
        name = str(item.get("name", "")).lower()
        matched_term = str(item.get("matched_term", "")).lower()
        path = str(item.get("path", "")).replace("\\", "/").lower()
        kind = str(item.get("kind", "")).lower()
        channel = str(item.get("recall_channel", ""))
        exact = 100 if matched_term and name == matched_term else 0
        normalized_identifier = matched_term.replace(".", "::")
        symbol_id = str(item.get("id", ""))
        qualified_value = str(item.get("qualified_name", "")) or (
            symbol_id.split("::", 1)[1] if "::" in symbol_id else ""
        )
        qualified = (
            120
            if any(marker in matched_term for marker in (".", "::"))
            and normalized_identifier in qualified_value.lower()
            else 0
        )
        structured = 40 if any(marker in matched_term for marker in ("_", ".", "::", "/")) else 0
        definition = 20 if kind in {"class", "function", "method", "file"} else 0
        role = 80 if cls._query_role_match(cls._query_profile(task), path, name) else 0
        path_match = 20 if matched_term and matched_term in path else 0
        alias = 10 if channel == "symbol_alias" else 0
        generic = -70 if name in GENERIC_SYMBOL_NAMES or matched_term in GENERIC_SYMBOL_NAMES else 0
        vendor = -65 if path.startswith(VENDOR_PATH_PREFIXES) else 0
        breakdown = {
            "exact_name": exact,
            "qualified_name": qualified,
            "structured_term": structured,
            "definition": definition,
            "query_role": role,
            "path_match": path_match,
            "alias": alias,
            "generic_symbol": generic,
            "vendor_source": vendor,
        }
        return sum(breakdown.values()), breakdown

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
            term_matches = sum(1 for term in terms if term in lowered)
            score = term_matches * 10
            if any(keyword in lowered for keyword in ("不变量", "必须", "不能", "不得", "回滚", "验证")):
                score += 3 if term_matches else 1
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
    def _score_breakdown(record: KnowledgeRecord, text_score: float, module: str | None, vector_similarity: float = 0.0) -> dict[str, float]:
        confidence = CONFIDENCE_WEIGHT.get(record.confidence, 0)
        freshness = FRESHNESS_WEIGHT.get(record.status, -1)
        kind_boost = 2.0 if record.kind in {"feature-guide", "development-guide"} else 0.0
        module_boost = 0.5 if module and module in record.tags else 0.0
        vector_boost = min(0.25, max(0.0, vector_similarity) * 0.25)
        return {"text_match": round(text_score, 4), "confidence": confidence, "freshness": freshness, "kind_boost": kind_boost, "module_boost": module_boost, "vector_similarity": round(vector_similarity, 4), "vector_boost": round(vector_boost, 4), "total": round(text_score + confidence + freshness + kind_boost + module_boost + vector_boost, 4)}

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
        references = [{"symbol": item["name"], "symbol_id": item["id"], "name": item["name"], "path": item["path"], "line": item.get("line"), "kind": item["kind"], "reason": "任务词与符号名称精确或前缀命中。"} for item in symbol_matches[:4]]
        if not references: references = [{"record": item["id"], "path": item["path"], "kind": item["kind"], "reason": item.get("why_selected", "")} for item in selected_results[:4]]
        return references

    @staticmethod
    def _extension_points(symbol_matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keywords = ("create", "add", "extend", "register", "save", "use", "新增", "扩展", "注册")
        return [{"symbol": item["name"], "symbol_id": item["id"], "name": item["name"], "path": item["path"], "line": item.get("line"), "reason": "可作为新增功能的现有实现或扩展锚点。"} for item in symbol_matches if item.get("kind") in {"function", "method", "class"} or any(keyword in item.get("name", "").lower() for keyword in keywords)][:4]

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
        result["withheld_files"] = [
            item for item in result.get("withheld_files", [])
            if item.get("reason_code") == "token_budget"
        ]
        result["rejected_files"] = []
        for item in result.get("knowledge", []):
            item["sources"] = item.get("sources", [])[:4]
            item["withheld_sources"] = item.get("withheld_sources", [])[:4]
        for key, limit in [("affected_files", 8), ("affected_tests", 4), ("affected_knowledge", 4)]:
            result["impact"][key] = result["impact"][key][:limit]
        result["impact"]["affected_modules"] = result["impact"]["affected_modules"][:8]
        result["reference_implementations"] = result.get("reference_implementations", [])[:1]
        result["extension_points"] = result.get("extension_points", [])[:1]
        explanation = result.get("retrieval_explanation", {})
        explanation["selected_records"] = explanation.get("selected_records", [])[:1]
        explanation["reference_implementations"] = result.get(
            "reference_implementations", []
        )[:1]
        explanation["reference_implementations"] = [
            {
                "symbol": item.get("symbol", item.get("record", "")),
                "path": item.get("path", ""),
            }
            for item in explanation["reference_implementations"]
        ]
        explanation["extension_points"] = [
            {
                "symbol": item.get("symbol", ""),
                "path": item.get("path", ""),
            }
            for item in result.get("extension_points", [])[:1]
        ]
        explanation.get("impact", {}).update({
            "files": explanation.get("impact", {}).get("files", [])[:2],
            "tests": explanation.get("impact", {}).get("tests", [])[:2],
        })

        def size() -> int:
            result["estimated_tokens"] = 0
            measured = approx_tokens(json.dumps(result, ensure_ascii=False))
            result["estimated_tokens"] = measured
            return approx_tokens(json.dumps(result, ensure_ascii=False))

        for _ in range(200):
            if size() <= budget:
                return
            rankings_with_breakdowns = [
                item for item in result.get("file_rankings", [])
                if item.get("score_breakdown")
            ]
            if rankings_with_breakdowns:
                rankings_with_breakdowns[-1].pop("score_breakdown", None)
                continue
            for field in ("protected", "requires_live_source", "selection_stage", "score"):
                ranking = next(
                    (item for item in reversed(result.get("file_rankings", [])) if field in item),
                    None,
                )
                if ranking is not None:
                    ranking.pop(field)
                    break
            else:
                ranking = None
            if ranking is not None:
                continue
            if result.get("ranking_reason_code") is None and "ranking_reason_code" in result:
                result.pop("ranking_reason_code")
                continue
            if result.get("protected_candidates_truncated") is False:
                result.pop("protected_candidates_truncated")
                continue
            supporting_files = result.get("supporting_files", [])
            if supporting_files:
                path = supporting_files.pop()
                result["files"] = [item for item in result.get("files", []) if item != path]
                result["file_rankings"] = [
                    item for item in result.get("file_rankings", [])
                    if item.get("path") != path
                ]
                result.setdefault("withheld_files", []).append({
                    "path": path,
                    "reason_code": "token_budget",
                })
                continue
            if result.get("rejected_files"):
                result["rejected_files"].pop()
                continue
            optional_list = next(
                (
                    items for items in (
                        result.get("reference_implementations", []),
                        result.get("extension_points", []),
                        explanation.get("reference_implementations", []),
                        explanation.get("extension_points", []),
                        explanation.get("selected_records", []),
                        explanation.get("unknowns", []),
                        explanation.get("impact", {}).get("files", []),
                        explanation.get("impact", {}).get("tests", []),
                        result.get("unknowns", []),
                        result.get("likely_modules", []),
                        result.get("verification_commands", []),
                    )
                    if items
                ),
                None,
            )
            if optional_list is not None:
                optional_list.pop()
                continue
            optional_explanation_key = next(
                (
                    key for key in (
                        "impact", "rationale", "signals", "reference_count",
                        "extension_point_count", "unknown_count",
                    )
                    if key in explanation
                ),
                None,
            )
            if optional_explanation_key is not None:
                explanation.pop(optional_explanation_key)
                continue
            empty_explanation_key = next(
                (
                    key for key in ("reference_implementations", "extension_points")
                    if key in explanation and not explanation[key]
                ),
                None,
            )
            if empty_explanation_key is not None:
                explanation.pop(empty_explanation_key)
                continue
            guidance = result.get("guidance_workflow")
            if isinstance(guidance, dict) and set(guidance) != {"available"}:
                result["guidance_workflow"] = {"available": bool(guidance.get("available"))}
                continue
            knowledge_sources = next(
                (
                    item.get("sources", [])
                    for item in result.get("knowledge", [])
                    if len(item.get("sources", [])) > 1
                ),
                None,
            )
            if knowledge_sources is not None:
                knowledge_sources.pop()
                continue
            contents = [item for item in result["knowledge"] if item.get("tokens", 0) > 60]
            if contents:
                longest = max(contents, key=lambda item: len(item["content"]))
                longest["content"] = trim_to_tokens(longest["content"], max(40, longest["tokens"] // 2))
                longest["tokens"] = approx_tokens(longest["content"])
                continue
            if len(result["knowledge"]) > 1:
                result["knowledge"].pop()
                continue
            impact_lists = [value for value in result["impact"].values() if isinstance(value, list) and value]
            if impact_lists:
                max(impact_lists, key=len).pop()
                continue
            if len(result["symbols"]) > 1:
                result["symbols"].pop()
                continue
            if len(result["gaps"]) > 1:
                result["gaps"].pop()
                continue
            result["summary"] = trim_to_tokens(result["summary"], 10)
            break
        size()

    @staticmethod
    def _record_query(store: KnowledgeStore, tool: str, input_size: int, result: dict[str, Any], started: float) -> None:
        store.record_query(
            utc_now(), tool, input_size, approx_tokens(json.dumps(result, ensure_ascii=False)), int((time.monotonic() - started) * 1000)
        )
