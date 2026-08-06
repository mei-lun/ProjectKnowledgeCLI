from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .models import KnowledgeRecord
from .service import ProjectService
from .store import KnowledgeStore
from .util import approx_tokens, trim_to_tokens, utc_now


CONFIDENCE_WEIGHT = {"verified": 1.0, "generated": 0.8, "inferred": 0.3}
FRESHNESS_WEIGHT = {"fresh": 1.0, "potentially_stale": -0.5, "stale": -1.5, "conflicted": -2.0}


class KnowledgeAPI:
    def __init__(self, project: str | Path = "."):
        self.service = ProjectService(project)
        self.root = self.service.root
        self.config = ProjectConfig.load(self.root)
        if not self.service.db_path.exists():
            raise RuntimeError(f"{self.root} is not initialized")

    def status(self) -> dict[str, Any]:
        return self.service.status()

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
            result["requires_live_source"] = bool(pending_sources) or record.status != "fresh" or record.confidence == "inferred"
            return result

    def search(self, query: str, kinds: list[str] | None = None, module: str | None = None, limit: int = 10) -> dict[str, Any]:
        started = time.monotonic()
        pending = set(self.service.status().get("pending_files", []))
        with KnowledgeStore(self.service.db_path) as store:
            matches = store.search_knowledge(query, max(1, min(limit * 2, 100)), kinds, module)
            ranked: list[tuple[KnowledgeRecord, float]] = []
            for record, text_score in matches:
                score = text_score + CONFIDENCE_WEIGHT.get(record.confidence, 0) + FRESHNESS_WEIGHT.get(record.status, -1)
                if module and module in record.tags:
                    score += 0.5
                ranked.append((record, score))
            ranked.sort(key=lambda item: (-item[1], item[0].id))
            items = []
            for record, score in ranked[:limit]:
                pending_sources = self._pending_sources(record, pending)
                summary = (
                    f"[withheld: depends on pending source {', '.join(pending_sources)}]"
                    if pending_sources else self._summary(record.content)
                )
                items.append({
                    "id": record.id, "title": record.title, "kind": record.kind, "path": record.path,
                    "ownership": record.ownership, "confidence": record.confidence,
                    "freshness": "potentially_stale" if pending_sources else record.status,
                    "score": round(score, 4), "summary": summary,
                    "sources": [source.to_dict() for source in record.sources],
                    "requires_live_source": bool(pending_sources) or record.status != "fresh" or record.confidence == "inferred",
                })
            result = {"query": query, "results": items, "gaps": [] if items else ["No matching knowledge record; search live source."]}
            self._record_query(store, "knowledge_search", len(query), result, started)
            return result

    def impact(self, files: list[str] | None = None, symbols: list[str] | None = None) -> dict[str, Any]:
        started = time.monotonic()
        files = [Path(path).as_posix().lstrip("./") for path in (files or [])]
        symbols = symbols or []
        with KnowledgeStore(self.service.db_path) as store:
            symbol_ids = set(symbols)
            if files:
                placeholders = ",".join("?" for _ in files)
                symbol_ids.update(row["id"] for row in store.rows(f"SELECT id FROM symbols WHERE path IN ({placeholders})", files))
            expanded = set(symbol_ids)
            relations: list[dict[str, Any]] = []
            if symbol_ids:
                placeholders = ",".join("?" for _ in symbol_ids)
                relations = store.rows(
                    f"SELECT source, target, kind, path, line, confidence, resolved FROM relations WHERE source IN ({placeholders}) OR target IN ({placeholders}) ORDER BY confidence DESC LIMIT 500",
                    [*symbol_ids, *symbol_ids],
                )
                for relation in relations:
                    expanded.add(relation["source"])
                    if relation["resolved"]:
                        expanded.add(relation["target"])
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
                "affected_files": sorted(impacted_paths),
                "affected_symbols": sorted(expanded),
                "affected_modules": modules,
                "affected_tests": tests,
                "affected_knowledge": knowledge,
                "relations": relations,
                "limitations": self.service.engine.status()["limitations"],
            }
            self._record_query(store, "knowledge_impact", len(json.dumps(result["input"])), result, started)
            return result

    def context(self, task: str, max_tokens: int | None = None) -> dict[str, Any]:
        started = time.monotonic()
        budget = max(256, min(max_tokens or self.config.max_tokens, 50_000))
        status = self.service.status()
        search = self.search(task, limit=10)
        broad_project_requested = any(
            phrase in task.lower()
            for phrase in ["project map", "repository overview", "project overview", "项目地图", "项目概览"]
        )
        selected_results = [
            item for item in search["results"]
            if broad_project_requested or item["kind"] != "project"
        ][:4]
        terms = [term for term in re.findall(r"[A-Za-z_$][\w$.:/-]{2,}|[\u4e00-\u9fff]{2,}", task) if len(term) > 1]
        with KnowledgeStore(self.service.db_path) as store:
            symbol_matches: list[dict[str, Any]] = []
            seen: set[str] = set()
            for term in terms[:12]:
                for row in store.rows(
                    "SELECT id, name, kind, path, line, confidence FROM symbols WHERE name LIKE ? OR id LIKE ? ORDER BY confidence DESC LIMIT 8",
                    [f"%{term}%", f"%{term}%"],
                ):
                    if row["id"] not in seen:
                        seen.add(row["id"])
                        symbol_matches.append(row)
            impact = self.impact(symbols=[item["id"] for item in symbol_matches[:10]]) if symbol_matches else {
                "affected_modules": [], "affected_tests": [], "affected_files": [], "affected_knowledge": []
            }
            verification = self._verification_commands()
            fragments: list[dict[str, Any]] = []
            remaining = budget
            for item in selected_results:
                record = store.get_knowledge(item["id"])
                if not record:
                    continue
                pending_sources = self._pending_sources(record, set(status.get("pending_files", [])))
                fragment_budget = min(900, max(120, remaining // max(1, 12 - len(fragments))))
                content = "" if pending_sources else trim_to_tokens(record.content, fragment_budget)
                cost = approx_tokens(content)
                if cost > remaining:
                    continue
                fragments.append({
                    "id": record.id, "title": record.title, "kind": record.kind,
                    "confidence": record.confidence,
                    "freshness": "potentially_stale" if pending_sources else record.status,
                    "content": content, "sources": [source.to_dict() for source in record.sources],
                    "next_step": "Read live sources before relying on this record." if pending_sources or record.status != "fresh" else "Use cited symbols or files as the next source anchors.",
                    "requires_live_source": bool(pending_sources) or record.status != "fresh" or record.confidence == "inferred",
                    "withheld_sources": pending_sources,
                    "tokens": cost,
                })
                remaining -= cost
            gaps: list[str] = list(search["gaps"])
            if status.get("pending_files"):
                gaps.append("The index has pending source changes; synchronize or read those files live.")
            if not symbol_matches:
                gaps.append("No exact symbol anchor matched the task terms.")
            result = {
                "task": task, "project": self.config.project_name,
                "index": {"commit": status.get("index_commit"), "pending_files": status.get("pending_files", [])},
                "summary": self._context_summary(fragments, impact),
                "knowledge": fragments,
                "symbols": symbol_matches[:30],
                "impact": {key: impact.get(key, []) for key in ["affected_modules", "affected_files", "affected_tests", "affected_knowledge"]},
                "verification_commands": verification,
                "gaps": gaps,
                "token_budget": budget,
                "estimated_tokens": 0,
            }
            self._fit_context(result, budget)
            self._record_query(store, "knowledge_context", len(task), result, started)
            return result

    @staticmethod
    def _summary(content: str, limit: int = 480) -> str:
        clean = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:limit] + ("..." if len(clean) > limit else "")

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
        result["symbols"] = result["symbols"][:20]
        for key in ["affected_files", "affected_tests", "affected_knowledge"]:
            result["impact"][key] = result["impact"][key][:30]
        result["impact"]["affected_modules"] = result["impact"]["affected_modules"][:20]

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
