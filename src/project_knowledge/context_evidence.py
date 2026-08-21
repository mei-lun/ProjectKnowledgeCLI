"""Deterministic required-evidence planning for retrieval context assembly.

The planner deliberately has no token-budget or evaluation-oracle input.  It
marks the smallest set of source facts that must survive later context
assembly, while leaving ranking and recall to their existing stages.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


_FRESH = {"fresh"}
_PATH_PROFILES = {"call_path", "impact"}
_EXACT_REASONS = {
    "exact",
    "exact_symbol",
    "exact_symbol_anchor",
    "qualified_exact",
    "symbol_exact",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _query_text(task: str | Mapping[str, Any]) -> str:
    if isinstance(task, Mapping):
        for key in ("query", "task", "text", "question"):
            value = task.get(key)
            if value:
                return _text(value)
        return " ".join(_text(value) for value in task.values() if value)
    return _text(task)


def _profile_name(profile: str | Mapping[str, Any] | None) -> str:
    if isinstance(profile, Mapping):
        for key in ("task_type", "query_type", "profile", "intent"):
            if profile.get(key):
                return _text(profile[key]).lower()
        return ""
    return _text(profile).lower()


def _is_fresh_resolved(item: Mapping[str, Any]) -> bool:
    freshness = _text(item.get("freshness", item.get("status", "fresh"))).lower()
    resolved = item.get("resolved", True)
    return freshness in _FRESH and resolved is not False


def _path_of(item: Mapping[str, Any]) -> str:
    return _text(item.get("path", item.get("file", item.get("source_path", "")))).replace("\\", "/")


def _symbol_id(item: Mapping[str, Any]) -> str:
    return _text(item.get("symbol_id", item.get("id", item.get("symbol", ""))))


def _contains_anchor(query: str, value: str) -> bool:
    value = _text(value)
    if not value:
        return False
    if any(ord(char) > 127 for char in value) or any(char in value for char in ":./\\$"):
        return value in query
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])", query) is not None


def _explicit_symbol_anchor(query: str, symbol: Mapping[str, Any]) -> bool:
    identifiers = (_symbol_id(symbol), _text(symbol.get("qualified_name")))
    query_lower = query.lower()
    if any(_contains_anchor(query_lower, identifier.lower()) for identifier in identifiers):
        return True
    reason = _text(symbol.get("match_reason", symbol.get("reason", ""))).lower()
    exact = symbol.get("exact") is True or symbol.get("is_exact") is True
    confidence = symbol.get("confidence")
    breakdown = symbol.get("symbol_score_breakdown", {})
    recall_channel = _text(symbol.get("recall_channel")).lower()
    try:
        exact_score = (
            float(breakdown.get("exact", 0) or 0)
            if isinstance(breakdown, Mapping) else 0.0
        )
        qualified_score = (
            float(breakdown.get("qualified", 0) or 0)
            if isinstance(breakdown, Mapping) else 0.0
        )
    except (TypeError, ValueError):
        exact_score = qualified_score = 0.0
    scored_exact = isinstance(breakdown, Mapping) and (
        exact_score > 0 or qualified_score > 0
    ) and recall_channel not in {"symbol_alias", "symbol_prefix"}
    matched_term = _text(symbol.get("matched_term")).lower()
    symbol_name = _text(symbol.get("name", symbol.get("short_name", ""))).lower()
    standalone_term = bool(matched_term) and re.search(
        rf"(?<![A-Za-z0-9_.]){re.escape(matched_term)}(?![A-Za-z0-9_.])",
        query_lower,
    ) is not None
    exact_term = (
        recall_channel in {"symbol_exact", "qualified_symbol_exact"}
        and bool(matched_term)
        and matched_term.rsplit(".", 1)[-1] == symbol_name
        and _contains_anchor(query_lower, matched_term)
        and (("." in matched_term or "::" in matched_term) or standalone_term)
    )
    return exact or scored_exact or exact_term or (
        (confidence == 1 or confidence == 1.0)
        and reason in _EXACT_REASONS
    )


def _span(symbol: Mapping[str, Any]) -> dict[str, int]:
    value = symbol.get("span")
    if isinstance(value, Mapping):
        result = {}
        for key in ("start", "end", "start_line", "end_line"):
            if isinstance(value.get(key), int):
                result[key] = int(value[key])
        return result
    result = {}
    for key in ("line", "start_line", "end_line"):
        if isinstance(symbol.get(key), int):
            result["start" if key in {"line", "start_line"} else "end"] = int(symbol[key])
    return result


def _canonical_edge(edge: Mapping[str, Any], fallback_order: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": _text(edge.get("source", edge.get("caller", ""))),
        "target": _text(edge.get("target", edge.get("callee", ""))),
        "kind": _text(edge.get("kind", edge.get("relation", "affected"))),
        "direction": _text(edge.get("direction", "")),
        "order": edge.get("order", edge.get("index", fallback_order)),
    }
    if not isinstance(result["order"], int):
        result["order"] = fallback_order
    for key in ("source_path", "target_path", "path", "line"):
        if edge.get(key) not in (None, ""):
            result[key] = edge[key]
    return result


class RequiredEvidencePlanner:
    """Build required evidence from already-ranked, source-backed candidates."""

    def plan(
        self,
        task: str | Mapping[str, Any],
        query_profile: str | Mapping[str, Any] | None,
        core_file_paths: Iterable[str],
        symbols: Iterable[Mapping[str, Any]],
        relations: Iterable[Mapping[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        query = _query_text(task)
        profile = _profile_name(query_profile)
        core = {_text(path).replace("\\", "/") for path in core_file_paths}
        source_symbols = [item for item in symbols if isinstance(item, Mapping)]
        symbol_by_id = {_symbol_id(item): item for item in source_symbols if _symbol_id(item)}
        required_symbols: list[dict[str, Any]] = []
        required_ids: set[str] = set()

        for symbol in source_symbols:
            symbol_id = _symbol_id(symbol)
            path = _path_of(symbol)
            if not symbol_id or not path or path not in core or not _is_fresh_resolved(symbol):
                continue
            if not _explicit_symbol_anchor(query, symbol):
                continue
            required_ids.add(symbol_id)
            required_symbols.append({
                "evidence_id": f"symbol:{symbol_id}",
                "kind": "symbol",
                "tier": "core",
                "retention": "required",
                "reason_code": "exact_symbol_anchor",
                "payload": {
                    "symbol_id": symbol_id,
                    "path": path,
                    "qualified_name": _text(symbol.get("qualified_name")),
                    "signature": _text(symbol.get("signature")),
                    "span": _span(symbol),
                },
            })

        required_paths: list[dict[str, Any]] = []
        if profile in _PATH_PROFILES and required_ids:
            grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            standalone: list[Mapping[str, Any]] = []
            for relation in relations:
                if not isinstance(relation, Mapping) or not _is_fresh_resolved(relation):
                    continue
                path_key = _text(relation.get("path_id", relation.get("relation_path_id", "")))
                if path_key:
                    grouped[path_key].append(relation)
                else:
                    standalone.append(relation)

            candidates: list[list[Mapping[str, Any]]] = list(grouped.values())
            candidates.extend([[item] for item in standalone])
            for candidate in candidates:
                edges = [_canonical_edge(edge, index) for index, edge in enumerate(candidate, 1)]
                edges.sort(key=lambda edge: (edge["order"], edge["source"], edge["target"]))
                if not edges or not any(edge["source"] in required_ids or edge["target"] in required_ids for edge in edges):
                    continue
                if any(not edge["source"] or not edge["target"] for edge in edges):
                    continue
                if not self._path_is_core(edges, core, symbol_by_id):
                    continue
                if not self._is_contiguous(edges):
                    continue
                canonical = {"edges": edges}
                digest = hashlib.sha256(
                    json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()[:16]
                required_paths.append({
                    "evidence_id": f"relation_path:{digest}",
                    "kind": "relation_path",
                    "tier": "core",
                    "retention": "required",
                    "reason_code": "exact_relation_path",
                    "payload": {"path_id": f"relation_path:{digest}", **canonical},
                })

        required_symbols.sort(key=lambda item: item["evidence_id"])
        required_paths.sort(key=lambda item: item["evidence_id"])
        return {
            "required_symbols": required_symbols,
            "required_relation_paths": required_paths,
            "evidence": [*required_symbols, *required_paths],
        }

    @staticmethod
    def _path_is_core(edges: list[dict[str, Any]], core: set[str], symbols: Mapping[str, Mapping[str, Any]]) -> bool:
        for edge in edges:
            paths: set[str] = set()
            for endpoint, path_key in (("source", "source_path"), ("target", "target_path")):
                identity = edge[endpoint]
                if identity in symbols:
                    path = _path_of(symbols[identity])
                    if path:
                        paths.add(path)
                elif edge.get(path_key):
                    # A relation endpoint that was not resolved to a
                    # canonical symbol is not safe to call required.  The
                    # file path alone cannot prove the edge's identity.
                    return False
                else:
                    # An unresolved endpoint cannot be proven to belong to Core.
                    return False
            if edge.get("path"):
                paths.add(_text(edge["path"]).replace("\\", "/"))
            if not paths.issubset(core):
                return False
        return True

    @staticmethod
    def _is_contiguous(edges: list[dict[str, Any]]) -> bool:
        return all(left["target"] == right["source"] for left, right in zip(edges, edges[1:]))


__all__ = ["RequiredEvidencePlanner"]
