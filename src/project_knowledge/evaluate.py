from __future__ import annotations

import json
import math
import os
import platform
import re
import statistics
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .ranking import DEFAULT_RANKING_POLICY, LEGACY_RANKING_POLICY, FileCandidate, rank_files
from .retrieval import KnowledgeAPI
from .store import KnowledgeStore
from .util import approx_tokens, hash_file, hash_text, read_text, trim_to_tokens, utc_now


SCHEMA_VERSION = 1
SUPPORTED_DATASET_SCHEMA_VERSIONS = {1, 2}
STRATEGIES = {"hybrid", "grep_read", "code", "markdown", "codegraph"}
GREP_RANKING_POLICY = replace(LEGACY_RANKING_POLICY, full_limit=7)
EXPECTED_LIST_FIELDS = {
    "expected_files",
    "expected_symbols",
    "expected_call_path",
    "expected_extension_points",
    "expected_invariants",
    "expected_design_reasons",
}
RECALL_FIELDS = {
    "expected_files": "file_recall",
    "expected_symbols": "symbol_recall",
    "expected_call_path": "call_path_recall",
    "expected_extension_points": "extension_point_recall",
    "expected_invariants": "invariant_recall",
    "expected_design_reasons": "design_reason_recall",
}
PRECISION_FIELDS = {
    "expected_files": "file_precision",
    "expected_symbols": "symbol_precision",
    "expected_call_path": "call_path_precision",
    "expected_extension_points": "extension_point_precision",
}
LOWER_IS_BETTER_METRICS = {
    "average_context_tokens",
    "average_core_files",
    "average_returned_files",
    "average_tool_calls",
    "p50_latency_ms",
    "p95_latency_ms",
    "p99_latency_ms",
    "ranking_fallback_rate",
}


def load_dataset(dataset: str | Path) -> list[dict[str, Any]]:
    path = Path(dataset)
    samples: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            sample = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"dataset line {number}: JSON 无效：{error.msg}") from error
        if not isinstance(sample, dict):
            raise ValueError(f"dataset line {number}: 必须是 JSON 对象")
        for field in ["schema_version", "id", "task", "category"]:
            if field not in sample:
                raise ValueError(f"dataset line {number}: 缺少必填字段 {field}")
        if sample["schema_version"] not in SUPPORTED_DATASET_SCHEMA_VERSIONS:
            raise ValueError(f"dataset line {number}: 不支持 schema_version={sample['schema_version']}")
        for field in ["id", "task", "category"]:
            if not isinstance(sample[field], str) or not sample[field].strip():
                raise ValueError(f"dataset line {number}: {field} 必须是非空字符串")
        if sample["id"] in identifiers:
            raise ValueError(f"dataset line {number}: id {sample['id']!r} 重复")
        identifiers.add(sample["id"])
        for field in EXPECTED_LIST_FIELDS:
            value = sample.get(field, [])
            if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
                raise ValueError(f"dataset line {number}: {field} 必须是非空字符串数组")
        if "acceptable_supporting_files" in sample:
            value = sample["acceptable_supporting_files"]
            if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
                raise ValueError(
                    f"dataset line {number}: acceptable_supporting_files 必须是非空字符串数组"
                )
        if sample["schema_version"] >= 2:
            _validate_required_evidence(sample.get("required_evidence"), number)
            if "expected_context_incomplete" in sample and not isinstance(
                sample["expected_context_incomplete"], bool
            ):
                raise ValueError(f"dataset line {number}: expected_context_incomplete 必须是布尔值")
        has_required_anchor = bool(
            _evidence_symbol_ids(sample.get("required_evidence"))
            or _evidence_path_keys(sample.get("required_evidence"))
        )
        if (
            not any(sample.get(field) for field in EXPECTED_LIST_FIELDS)
            and not has_required_anchor
            and "expected_stale" not in sample
        ):
            raise ValueError(f"dataset line {number}: 至少需要一个期望锚点或 expected_stale")
        if "expected_stale" in sample and not isinstance(sample["expected_stale"], bool):
            raise ValueError(f"dataset line {number}: expected_stale 必须是布尔值")
        if "max_tokens" in sample and (not isinstance(sample["max_tokens"], int) or sample["max_tokens"] < 256):
            raise ValueError(f"dataset line {number}: max_tokens 必须是大于等于 256 的整数")
        sample["line"] = number
        samples.append(sample)
    if not samples:
        raise ValueError("dataset 不包含评测样本")
    return samples


def evaluate(
    project: str | Path,
    dataset: str | Path,
    strategy: str = "hybrid",
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown evaluation strategy: {strategy}")
    api = KnowledgeAPI(project)
    if strategy == "codegraph":
        diagnostic = api.service.engine.diagnose(api.root)
        if not diagnostic.get("available"):
            return {
                "schema_version": SCHEMA_VERSION,
                "strategy": strategy,
                "available": False,
                "reason_code": "adapter_unavailable",
                "details": [diagnostic.get("reason_code", "command_failed"), diagnostic.get("details", "")],
                "adapter": diagnostic,
                "message": "CodeGraph adapter probe did not pass.",
            }

    dataset_path = Path(dataset)
    samples = load_dataset(dataset_path)
    if limit is not None:
        if limit < 1:
            raise ValueError("evaluation limit must be at least 1")
        samples = samples[:limit]
    results = [_evaluate_sample(api, sample, strategy) for sample in samples]
    status = api.status()
    source_snapshot = hash_text("\n".join(
        f"{item.path}\t{item.content_hash}"
        for item in api.service.engine.snapshot(api.root, api.config).files
    ))
    metrics, metric_counts = _aggregate(results)
    stage_metrics = _aggregate_stage_metrics(results)
    with KnowledgeStore(api.service.db_path, readonly=True) as store:
        generated = [record for record in store.all_knowledge() if record.ownership == "generated"]
    sourced = sum(bool(record.sources) for record in generated)
    metrics["generated_source_coverage"] = sourced / max(1, len(generated))
    metrics = {key: round(value, 6) for key, value in metrics.items()}
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy": strategy,
        "available": True,
        "samples": len(results),
        "metrics": metrics,
        "metric_sample_counts": metric_counts,
        "stage_metrics": stage_metrics,
        "category_success": _category_success(results),
        "failure_samples": [item["id"] for item in results if not item["success"]],
        "reproducibility": {
            "generated_at": utc_now(),
            "dataset": dataset_path.name,
            "dataset_sha256": hash_file(dataset_path),
            "project": api.config.project_name,
            "head_commit": status.get("head_commit"),
            "index_commit": status.get("index_commit"),
            "working_tree": status.get("working_tree"),
            "source_snapshot_sha256": source_snapshot,
            "project_files": status.get("counts", {}).get("files", 0),
            "project_symbols": status.get("counts", {}).get("symbols", 0),
            "engine": status.get("engine", {}),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "cpu_count": os.cpu_count(),
        },
        "results": results,
    }


def evaluate_suite(
    project: str | Path,
    dataset: str | Path,
    *,
    strategies: Iterable[str] | None = None,
    thresholds: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    selected = list(dict.fromkeys(strategies or ["hybrid"]))
    if not selected:
        raise ValueError("at least one evaluation strategy is required")
    reports = {strategy: evaluate(project, dataset, strategy, limit=limit) for strategy in selected}
    suite: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "dataset": Path(dataset).name,
        "dataset_sha256": hash_file(Path(dataset)),
        "strategies": reports,
    }
    reproducibility = next(
        (
            strategy_report.get("reproducibility", {})
            for strategy_report in reports.values()
            if strategy_report.get("available")
        ),
        {},
    )
    suite.update({
        "generated_at": utc_now(),
        "project_commit": reproducibility.get("head_commit"),
        "index_commit": reproducibility.get("index_commit"),
        "working_tree": reproducibility.get("working_tree"),
        "source_snapshot_sha256": reproducibility.get("source_snapshot_sha256"),
        "package_version": __version__,
    })
    suite["quality_gate"] = (
        evaluate_quality_gate(suite, thresholds, baseline)
        if thresholds is not None
        else {"evaluated": False, "passed": None, "failures": [], "warnings": []}
    )
    return suite


def evaluate_quality_gate(
    report: dict[str, Any],
    thresholds: dict[str, Any],
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    strategies = report.get("strategies", {})
    required = thresholds.get("required_strategies", ["hybrid"])
    allowed_unavailable = set(thresholds.get("allowed_unavailable_strategies", []))
    minimum_samples = int(thresholds.get("minimum_samples", 1))
    strategy_thresholds = thresholds.get("strategies", {})
    comparable_baseline = baseline
    if baseline and baseline.get("quality_gate", {}).get("passed") is False:
        failures.append({
            "code": "invalid_baseline",
            "message": "baseline quality gate did not pass; refusing to use a failed report for regression comparison",
        })
    if baseline:
        current_dataset = report.get("dataset_sha256")
        baseline_dataset = baseline.get("dataset_sha256")
        if current_dataset and baseline_dataset and current_dataset != baseline_dataset:
            warnings.append({
                "code": "baseline_dataset_mismatch",
                "current_dataset_sha256": current_dataset,
                "baseline_dataset_sha256": baseline_dataset,
                "message": "数据集已变化；只执行绝对阈值，不比较不可比的汇总回归。",
            })
            comparable_baseline = None

    for strategy in required:
        strategy_report = strategies.get(strategy)
        if strategy_report is None:
            failures.append({"code": "missing_strategy", "strategy": strategy})
            continue
        if not strategy_report.get("available", False):
            item = {
                "code": "strategy_unavailable",
                "strategy": strategy,
                "reason_code": strategy_report.get("reason_code"),
            }
            (warnings if strategy in allowed_unavailable else failures).append(item)
            continue
        configured = strategy_thresholds.get(strategy, {})
        strategy_minimum_samples = int(configured.get("minimum_samples", minimum_samples))
        if strategy_report.get("samples", 0) < strategy_minimum_samples:
            failures.append({
                "code": "insufficient_samples",
                "strategy": strategy,
                "expected": strategy_minimum_samples,
                "actual": strategy_report.get("samples", 0),
            })
        metrics = strategy_report.get("metrics", {})
        minimum_metrics = {**thresholds.get("minimum", {}), **configured.get("minimum", {})}
        maximum_metrics_for_strategy = {**thresholds.get("maximum", {}), **configured.get("maximum", {})}
        for metric, expected in minimum_metrics.items():
            actual = metrics.get(metric)
            if actual is None or actual < expected:
                failures.append({
                    "code": "metric_below_minimum", "strategy": strategy,
                    "metric": metric, "expected": expected, "actual": actual,
                })
        for metric, expected in maximum_metrics_for_strategy.items():
            actual = metrics.get(metric)
            if actual is None or actual > expected:
                failures.append({
                    "code": "metric_above_maximum", "strategy": strategy,
                    "metric": metric, "expected": expected, "actual": actual,
                })

    if comparable_baseline:
        baseline_strategies = comparable_baseline.get("strategies", {})
        for strategy, strategy_report in strategies.items():
            if not strategy_report.get("available", False):
                continue
            previous = baseline_strategies.get(strategy, {}).get("metrics", {})
            current = strategy_report.get("metrics", {})
            maximum_metrics = {
                *thresholds.get("maximum", {}),
                *strategy_thresholds.get(strategy, {}).get("maximum", {}),
            }
            for metric, tolerance in thresholds.get("allowed_regression", {}).items():
                if metric not in previous or metric not in current:
                    continue
                regressed = (
                    current[metric] - previous[metric] > tolerance
                    if metric in maximum_metrics or metric in LOWER_IS_BETTER_METRICS
                    else previous[metric] - current[metric] > tolerance
                )
                if regressed:
                    failures.append({
                        "code": "metric_regression", "strategy": strategy, "metric": metric,
                        "baseline": previous[metric], "actual": current[metric], "tolerance": tolerance,
                    })
    return {
        "evaluated": True,
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "threshold_schema_version": thresholds.get("schema_version", 1),
    }


def load_json_object(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _evaluate_sample(api: KnowledgeAPI, sample: dict[str, Any], strategy: str) -> dict[str, Any]:
    started = time.monotonic()
    returned = _retrieve(api, sample, strategy)
    latency_ms = (time.monotonic() - started) * 1000
    expected = {field: set(sample.get(field, [])) for field in EXPECTED_LIST_FIELDS}
    actual = {
        "expected_files": set(returned["files"]),
        "expected_symbols": set(returned["symbols"]),
        "expected_call_path": set(returned["call_path"]),
        "expected_extension_points": set(returned["symbols"]),
    }
    metrics: dict[str, float] = {}
    failures: list[str] = []
    core_failures: list[str] = []
    for field, metric in RECALL_FIELDS.items():
        values = expected[field]
        if not values:
            continue
        if field in {"expected_invariants", "expected_design_reasons"}:
            matched = {item for item in values if _contains_text(returned["text"], item)}
            recall = len(matched) / len(values)
        else:
            matched = values & actual[field]
            recall = len(matched) / len(values)
        metrics[metric] = recall
        if recall < 1:
            failures.append(metric)
        precision_metric = PRECISION_FIELDS.get(field)
        if precision_metric:
            metrics[precision_metric] = len(matched) / max(1, len(actual[field]))

    expected_files = expected["expected_files"]
    pre_budget_files = set(returned.get("pre_budget_files", returned["files"]))
    pre_budget_core_files = set(
        returned.get("pre_budget_core_files", returned["core_files"])
    )
    pre_budget_matched = expected_files & pre_budget_files
    pre_budget_core_matched = expected_files & pre_budget_core_files
    metrics["pre_budget_file_recall"] = (
        len(pre_budget_matched) / len(expected_files) if expected_files else 0.0
    )
    metrics["pre_budget_file_precision"] = (
        len(pre_budget_matched) / max(1, len(pre_budget_files))
    )
    metrics["pre_budget_core_file_recall"] = (
        len(pre_budget_core_matched) / len(expected_files) if expected_files else 0.0
    )
    metrics["pre_budget_core_file_precision"] = (
        len(pre_budget_core_matched) / max(1, len(pre_budget_core_files))
    )
    metrics["pre_budget_ndcg_at_5"] = _ndcg_at_k(
        returned.get("pre_budget_core_files", returned["core_files"]),
        expected_files,
    )
    core_files = set(returned["core_files"])
    core_matched = expected_files & core_files
    core_recall = len(core_matched) / len(expected_files) if expected_files else 0.0
    metrics["core_file_recall"] = core_recall
    metrics["core_file_precision"] = len(core_matched) / max(1, len(core_files))
    metrics["ndcg_at_5"] = _ndcg_at_k(returned["core_files"], expected_files)
    if expected_files and core_recall < 1:
        core_failures.append("core_file_recall")

    if "acceptable_supporting_files" in sample:
        supporting_files = set(returned["supporting_files"])
        acceptable_supporting_files = set(sample["acceptable_supporting_files"])
        metrics["acceptable_supporting_precision"] = (
            len(supporting_files & acceptable_supporting_files) / len(supporting_files)
            if supporting_files else 0.0
        )

    ranking_fallback = returned["ranking_status"] == "fallback"
    metrics["ranking_fallback"] = 1.0 if ranking_fallback else 0.0
    if ranking_fallback:
        failures.append("ranking_status")

    if "expected_stale" in sample:
        stale_match = bool(returned["stale_detected"]) == sample["expected_stale"]
        metrics["stale_detection"] = 1.0 if stale_match else 0.0
        if not stale_match:
            failures.append("stale_detection")

    if sample.get("schema_version", 1) >= 2:
        required = sample.get("required_evidence", {})
        oracle_symbols = _evidence_symbol_ids(required)
        oracle_paths = _evidence_path_keys(required)
        pre_evidence = returned.get("pre_required_evidence")
        post_evidence = returned.get("post_required_evidence")
        if pre_evidence is None:
            pre_evidence = returned.get("required_evidence", {})
        if post_evidence is None:
            post_evidence = returned.get("required_evidence", {})
        pre_symbols = _evidence_symbol_ids(pre_evidence)
        post_symbols = _evidence_symbol_ids(post_evidence)
        pre_paths = _evidence_path_keys(pre_evidence)
        post_paths = _evidence_path_keys(post_evidence)
        metrics["required_symbol_label_recall"] = (
            len(oracle_symbols & pre_symbols) / len(oracle_symbols) if oracle_symbols else 1.0
        )
        metrics["required_relation_path_label_recall"] = (
            len(oracle_paths & pre_paths) / len(oracle_paths) if oracle_paths else 1.0
        )
        metrics["pre_required_symbol_recall"] = metrics["required_symbol_label_recall"]
        metrics["pre_required_relation_path_recall"] = metrics[
            "required_relation_path_label_recall"
        ]
        metrics["post_required_symbol_recall"] = (
            len(oracle_symbols & post_symbols) / len(oracle_symbols) if oracle_symbols else 1.0
        )
        metrics["post_required_relation_path_recall"] = (
            len(oracle_paths & post_paths) / len(oracle_paths) if oracle_paths else 1.0
        )
        metrics["required_symbol_retention"] = (
            len(pre_symbols & post_symbols) / len(pre_symbols) if pre_symbols else 1.0
        )
        pre_symbol_payloads = _evidence_symbol_payloads(pre_evidence)
        post_symbol_payloads = _evidence_symbol_payloads(post_evidence)
        retained_symbol_payloads = sum(
            _symbol_payload_preserved(payload, post_symbol_payloads.get(symbol_id))
            for symbol_id, payload in pre_symbol_payloads.items()
        )
        metrics["required_symbol_payload_retention"] = (
            retained_symbol_payloads / len(pre_symbol_payloads)
            if pre_symbol_payloads else 1.0
        )
        metrics["required_relation_path_retention"] = (
            len(pre_paths & post_paths) / len(pre_paths) if pre_paths else 1.0
        )
        for metric in (
            "required_symbol_label_recall",
            "required_relation_path_label_recall",
            "required_symbol_retention",
            "required_symbol_payload_retention",
            "required_relation_path_retention",
        ):
            if metrics[metric] < 1:
                failures.append(metric)
        actual_incomplete = bool(returned.get("context_incomplete", False))
        evidence_incomplete = not pre_symbols.issubset(post_symbols) or not pre_paths.issubset(post_paths)
        incomplete_consistent = actual_incomplete == evidence_incomplete
        if "expected_context_incomplete" in sample:
            incomplete_consistent = (
                incomplete_consistent
                and actual_incomplete == sample["expected_context_incomplete"]
            )
        metrics["context_incomplete_consistency"] = 1.0 if incomplete_consistent else 0.0
        if not incomplete_consistent:
            failures.append("context_incomplete_consistency")

    context_tokens = approx_tokens(returned["text"])
    return {
        "line": sample["line"],
        "id": sample["id"],
        "task": sample["task"],
        "category": sample["category"],
        "answer_status": sample.get("answer_status", "candidate"),
        "metrics": {key: round(value, 6) for key, value in metrics.items()},
        "success": not failures,
        "failed_metrics": failures,
        "core_failed_metrics": core_failures,
        "estimated_context_tokens": context_tokens,
        "tool_calls": returned["tool_calls"],
        "latency_ms": round(latency_ms, 3),
        "returned_files": sorted(returned["files"]),
        "returned_symbols": sorted(returned["symbols"]),
        "returned_call_path": sorted(returned["call_path"]),
        "core_files": returned["core_files"],
        "supporting_files": returned["supporting_files"],
        "optional_files": returned.get("optional_files", []),
        "file_rankings": returned["file_rankings"],
        "pre_budget_files": returned.get("pre_budget_files", returned["files"]),
        "pre_budget_core_files": returned.get(
            "pre_budget_core_files", returned["core_files"]
        ),
        "pre_budget_file_rankings": returned.get(
            "pre_budget_file_rankings", returned["file_rankings"]
        ),
        "ranking_status": returned["ranking_status"],
        "selection_reasons": returned.get("selection_reasons", {}),
        "stale_detected": returned["stale_detected"],
        "context_incomplete": bool(returned.get("context_incomplete", False)),
        "context_status": returned.get("context_status", {}),
        "retrieval_trace": returned.get("retrieval_trace", {}),
        "pre_required_evidence": returned.get("pre_required_evidence", {}),
        "post_required_evidence": returned.get("post_required_evidence", {}),
    }


def _validate_required_evidence(value: Any, line: int) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"dataset line {line}: required_evidence 必须是对象")
    for field in ("symbols", "relation_paths"):
        items = value.get(field, [])
        if not isinstance(items, list):
            raise ValueError(f"dataset line {line}: required_evidence.{field} 必须是数组")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"dataset line {line}: required_evidence.{field} 项必须是对象")
            if field == "symbols":
                if not isinstance(item.get("id"), str) or not item["id"].strip():
                    raise ValueError(f"dataset line {line}: required_evidence.symbols.id 必须是非空字符串")
                for name in ("signature", "path"):
                    if name in item and (not isinstance(item[name], str) or not item[name].strip()):
                        raise ValueError(f"dataset line {line}: required_evidence.symbols.{name} 必须是非空字符串")
                if "span" in item:
                    span = item["span"]
                    if (
                        not isinstance(span, dict)
                        or not span
                        or any(
                            key not in {"start", "end", "start_line", "end_line"}
                            or not isinstance(value, int)
                            or isinstance(value, bool)
                            for key, value in span.items()
                        )
                    ):
                        raise ValueError(
                            f"dataset line {line}: required_evidence.symbols.span 必须包含整数行号"
                        )
            else:
                edges = item.get("edges")
                if not isinstance(edges, list) or not edges:
                    raise ValueError(f"dataset line {line}: required_evidence.relation_paths.edges 不能为空")
                previous_target: str | None = None
                for edge in edges:
                    if not isinstance(edge, dict) or any(
                        not isinstance(edge.get(name), str) or not edge[name].strip()
                        for name in ("source", "kind", "target")
                    ):
                        raise ValueError(f"dataset line {line}: relation_paths.edges 必须包含非空 source/kind/target")
                    if previous_target is not None and edge["source"] != previous_target:
                        raise ValueError(f"dataset line {line}: relation_paths 必须保持路径连续")
                    previous_target = edge["target"]


def _evidence_symbol_ids(value: Any) -> set[str]:
    if not isinstance(value, dict):
        return set()
    result: set[str] = set()
    for item in value.get("symbols", []):
        if isinstance(item, str) and item:
            result.add(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            result.add(item["id"])
    return result


def _evidence_symbol_payloads(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in value.get("symbols", []):
        if isinstance(item, str) and item:
            result[item] = {"id": item}
        elif isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            result[item["id"]] = item
    return result


def _symbol_payload_preserved(
    expected: dict[str, Any], actual: dict[str, Any] | None
) -> bool:
    if actual is None:
        return False
    for field in ("signature", "span"):
        value = expected.get(field)
        if value not in (None, "", {}) and actual.get(field) != value:
            return False
    return True


def _evidence_path_keys(value: Any) -> set[tuple[tuple[str, str, str], ...]]:
    if not isinstance(value, dict):
        return set()
    result: set[tuple[tuple[str, str, str], ...]] = set()
    for item in value.get("relation_paths", []):
        if not isinstance(item, dict) or not isinstance(item.get("edges"), list):
            continue
        edges = item["edges"]
        key = tuple(
            (edge.get("source", ""), edge.get("kind", ""), edge.get("target", ""))
            for edge in edges
            if isinstance(edge, dict)
        )
        continuous = all(key[index - 1][2] == key[index][0] for index in range(1, len(key)))
        if key and continuous and all(all(part for part in edge) for edge in key):
            result.add(key)
    return result


def _select_markdown_pages(results: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    if limit < 1:
        return []
    selected = list(results[:limit])
    has_source_module = any(
        item.get("kind") == "module" and not str(item.get("id", "")).endswith(".tests")
        for item in selected
    )
    if has_source_module:
        return selected
    candidate = next(
        (
            item for item in results[limit:]
            if item.get("kind") == "module"
            and not str(item.get("id", "")).endswith(".tests")
        ),
        None,
    )
    if candidate is None:
        return selected
    relevance_floor = float(selected[-1].get("score", 0.0)) * 0.8 if selected else 0.0
    if float(candidate.get("score", 0.0)) < relevance_floor:
        return selected
    replaceable = [
        index for index, item in enumerate(selected)
        if item.get("kind") not in {"module", "feature-guide"}
    ]
    if replaceable:
        selected[replaceable[-1]] = candidate
    return selected


def _matching_terms(value: str, terms: list[str]) -> set[str]:
    lowered = value.lower()
    return {term for term in terms if term in lowered}


def _normalized_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _selection_reasons(file_rankings: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    return {
        str(item["path"]): {
            "stage": str(item.get("selection_stage", "fallback")),
            "anchor": str(item.get("why_selected", "")),
        }
        for item in file_rankings
    }


def _ndcg_at_k(ranked: list[str], relevant: set[str], k: int = 5) -> float:
    if not relevant:
        return 0.0
    dcg = sum(
        (1.0 / math.log2(index + 2)) if path in relevant else 0.0
        for index, path in enumerate(ranked[:k])
    )
    ideal_hits = min(k, len(relevant))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    return dcg / ideal if ideal else 0.0


def _context_ranking_contract(
    context: dict[str, Any],
    *,
    exclude_knowledge_sources: bool = False,
    ranking_fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = ranking_fallback or {}
    core_files = list(context.get("core_files", []))
    supporting_files = list(context.get("supporting_files", []))
    optional_files = list(context.get("optional_files", []))
    files = list(context.get("files", core_files + supporting_files))
    file_rankings = context.get("file_rankings")
    if not isinstance(file_rankings, list):
        selected_paths = set(core_files + supporting_files + optional_files)
        file_rankings = [
            item for item in fallback.get("file_rankings", [])
            if item.get("path") in selected_paths
        ]
    ranking_status = context.get(
        "ranking_status", fallback.get("ranking_status", "unavailable")
    )
    if not exclude_knowledge_sources:
        return {
            "core_files": core_files,
            "supporting_files": supporting_files,
            "optional_files": optional_files,
            "files": files,
            "file_rankings": file_rankings,
            "ranking_status": ranking_status,
        }

    file_rankings = [
        item for item in file_rankings
        if item.get("selection_stage") != "knowledge_source"
    ]
    core_files = [item["path"] for item in file_rankings if item.get("tier") == "core"]
    supporting_files = [
        item["path"] for item in file_rankings if item.get("tier") == "supporting"
    ]
    optional_files = [
        item["path"] for item in file_rankings if item.get("tier") == "optional"
    ]
    return {
        "core_files": core_files,
        "supporting_files": supporting_files,
        "optional_files": optional_files,
        "files": list(dict.fromkeys(core_files + supporting_files)),
        "file_rankings": file_rankings,
        "ranking_status": ranking_status,
    }


def _retrieve(api: KnowledgeAPI, sample: dict[str, Any], strategy: str) -> dict[str, Any]:
    task = sample["task"]
    budget = sample.get("max_tokens", 4000)
    if strategy in {"hybrid", "code", "codegraph"}:
        context, diagnostics = api.context_for_evaluation(task, budget)
        direct_symbols = context.get("symbols", [])[:12]
        symbols = {item["id"] for item in direct_symbols}
        pre_budget_ranking = _context_ranking_contract(
            diagnostics.get("ranking_contract", {}),
            exclude_knowledge_sources=strategy == "code",
        )
        ranking_contract = _context_ranking_contract(
            context,
            exclude_knowledge_sources=strategy == "code",
            ranking_fallback=pre_budget_ranking,
        )
        text_parts: list[str] = []
        stale_detected = False
        if strategy == "hybrid":
            for record in context.get("knowledge", []):
                text_parts.append(record.get("content", ""))
                stale_detected = stale_detected or bool(record.get("requires_live_source"))
        call_path = set(symbols)
        call_path.update(context.get("impact", {}).get("call_path", []))
        compact = {"symbols": sorted(symbols), "files": ranking_contract["files"]}
        if strategy == "hybrid":
            compact["summary"] = context.get("summary")
        text_parts.append(json.dumps(compact, ensure_ascii=False, separators=(",", ":")))
        return {
            **ranking_contract,
            "symbols": symbols, "call_path": call_path,
            "text": "\n".join(text_parts), "stale_detected": stale_detected,
            "tool_calls": 4 if strategy == "hybrid" else 3,
            "selection_reasons": _selection_reasons(ranking_contract["file_rankings"]),
            "pre_budget_files": pre_budget_ranking["files"],
            "pre_budget_core_files": pre_budget_ranking["core_files"],
            "pre_budget_file_rankings": pre_budget_ranking["file_rankings"],
            "pre_required_evidence": context.get("pre_required_evidence", {}),
            "post_required_evidence": context.get("post_required_evidence", {}),
            "context_incomplete": bool(context.get("context_incomplete", False)),
            "context_status": context.get("context_status", {}),
            "retrieval_trace": diagnostics.get("retrieval_trace", {}),
            "missing_required_evidence": context.get("missing_required_evidence", []),
            "budget_status": context.get("budget_status"),
            "minimum_required_tokens": context.get("minimum_required_tokens", 0),
        }

    if strategy == "markdown":
        search = api.search(task, limit=10)
        symbols: set[str] = set()
        text_parts: list[str] = []
        fragments: list[dict[str, Any]] = []
        direct_paths: list[str] = []
        stale_detected = False
        reads = 0
        remaining = budget
        selected_pages = _select_markdown_pages(search["results"], limit=3)
        for item in selected_pages:
            sources = list(item.get("sources", []))
            if item.get("kind") == "decision" and item.get("path"):
                sources.append({"path": item["path"], "id": item.get("id", "")})
            source_evidence = [
                value
                for source in sources
                for value in (str(source.get("path", "")), str(source.get("id", "")))
                if value
            ]
            fragment = {
                "id": item.get("id", ""),
                "freshness": item.get("freshness", "fresh"),
                "requires_live_source": bool(item.get("requires_live_source")),
                "content": "\n".join(
                    [str(item.get("summary", "")), *source_evidence]
                ),
                "sources": sources,
            }
            fragments.append(fragment)
            if fragment["freshness"] == "fresh" and not fragment["requires_live_source"]:
                direct_paths.extend(
                    _normalized_path(str(source.get("path", "")))
                    for source in sources
                    if source.get("path")
                )
        for index, item in enumerate(selected_pages):
            if remaining <= 0:
                break
            reads += 1
            record = api.get(item["id"])
            sources = list(record.get("sources", []))
            if item.get("kind") == "decision" and item.get("path"):
                sources.append({"path": item["path"], "id": item.get("id", "")})
            symbols.update(source.get("id") for source in sources if source.get("id"))
            page_budget = max(1, remaining // (len(selected_pages) - index))
            content = _relevant_excerpt(
                record.get("content", item.get("summary", "")), task, page_budget
            )
            if content:
                text_parts.append(content)
                remaining -= approx_tokens(content)
            stale_detected = stale_detected or bool(record.get("requires_live_source"))

        impact_used = bool(direct_paths)
        impact = (
            api.impact(
                files=list(dict.fromkeys(direct_paths))[:8],
                max_hops=2,
                max_relations=100,
            )
            if impact_used
            else {"relations": [], "affected_files": [], "dependency_files": [], "affected_tests": []}
        )
        bounded_impact = {
            **impact,
            "relations": list(impact.get("relations", []))[:100],
            "affected_files": list(impact.get("affected_files", []))[:13],
            "dependency_files": list(impact.get("dependency_files", []))[:13],
            "affected_tests": list(impact.get("affected_tests", []))[:4],
        }
        candidates, allowed_paths = api._context_file_candidates(
            task,
            api.classify_task(task),
            [],
            bounded_impact,
            fragments,
        )
        ranked_files = rank_files(candidates, allowed_paths=allowed_paths)
        ranking_contract = ranked_files.to_dict()
        return {
            **ranking_contract,
            "symbols": symbols, "call_path": set(symbols),
            "text": "\n".join(text_parts), "stale_detected": stale_detected,
            "tool_calls": 1 + reads + int(impact_used),
            "selection_reasons": _selection_reasons(ranking_contract["file_rankings"]),
        }

    terms = _task_terms(task)
    intent = api.classify_task(task)
    candidates: list[FileCandidate] = []
    contents: dict[str, str] = {}
    allowed_paths: set[str] = set()
    symbols_by_path: dict[str, list[dict[str, Any]]] = {}
    for term in terms[:12]:
        for symbol in api.service.engine.search_symbols(api.root, api.config, term, limit=10):
            symbols_by_path.setdefault(_normalized_path(symbol.path), []).append({
                "id": symbol.id,
                "name": symbol.name,
                "path": symbol.path,
            })
    for item in api.service.engine.snapshot(api.root, api.config).files:
        path = _normalized_path(item.path)
        allowed_paths.add(path)
        content = read_text(api.root / path)
        lowered = content.lower()
        content_terms = {term for term in terms if term in lowered}
        if not content_terms:
            continue
        path_terms = _matching_terms(path, terms)
        file_symbols = symbols_by_path.get(path, [])
        symbol_terms = {
            term
            for term in terms
            if any(
                term in str(symbol[field]).lower()
                for symbol in file_symbols
                for field in ("id", "name")
            )
        }
        exact_symbol = any(
            term == str(symbol["name"]).lower()
            or term == str(symbol["id"]).lower()
            or str(symbol["id"]).lower().endswith(f"::{term}")
            for symbol in file_symbols
            for term in terms
        )
        filename = Path(path).name.lower()
        stem = Path(path).stem.lower()
        module = str(item.module).lower()
        is_test = "test" in path.lower()
        production_source_role = (
            not is_test
            and Path(path).suffix.lower() in {
                ".c", ".cc", ".cpp", ".cs", ".go", ".java", ".js", ".kt",
                ".lua", ".php", ".py", ".rb", ".rs", ".scala", ".sh",
                ".swift", ".ts",
            }
        )
        contents[path] = content
        candidates.append(FileCandidate(
            path=path,
            stages={"direct_symbol" if symbol_terms else "fallback"},
            anchors={
                str(symbol["id"])
                for symbol in file_symbols
                if any(term in str(symbol["id"]).lower() for term in symbol_terms)
            } or {"grep_match"},
            exact_symbol=exact_symbol,
            qualified_symbol=(
                not exact_symbol
                and any(
                    str(symbol["name"]).lower().startswith(term)
                    for symbol in file_symbols
                    for term in terms
                )
            ),
            exact_path=path.lower() in terms,
            exact_filename=filename in terms or stem in terms,
            exact_module=module in terms,
            module=str(item.module),
            task_role_match=(
                bool(path_terms or symbol_terms)
                or (
                    production_source_role
                    and len(content_terms) >= 4
                    and intent.get("task_type") in {
                        "investigation", "impact_analysis", "new_feature", "bug_fix", "refactor",
                    }
                )
            ),
            path_terms=path_terms,
            symbol_terms=symbol_terms,
            content_terms=content_terms,
            is_test=is_test,
            original_order=len(candidates),
        ))
    ranked_files = rank_files(
        candidates,
        allowed_paths=allowed_paths,
        policy=GREP_RANKING_POLICY,
    )
    ranking_contract = ranked_files.to_dict()
    return {
        **ranking_contract,
        "symbols": set(), "call_path": set(),
        "text": "\n".join(contents[path][:4000] for path in ranking_contract["files"]),
        "stale_detected": False,
        "tool_calls": 1 + len(ranking_contract["files"]),
        "selection_reasons": _selection_reasons(ranking_contract["file_rankings"]),
    }


def _aggregate(results: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, int]]:
    names = set().union(*(item["metrics"].keys() for item in results))
    metrics: dict[str, float] = {}
    counts: dict[str, int] = {}
    for name in sorted(names):
        if name == "ranking_fallback":
            continue
        values = [item["metrics"][name] for item in results if name in item["metrics"]]
        metrics[name] = statistics.fmean(values)
        counts[name] = len(values)
    latencies = [item["latency_ms"] for item in results]
    metrics.update({
        "success_rate": statistics.fmean(1.0 if item["success"] else 0.0 for item in results),
        "average_context_tokens": statistics.fmean(item["estimated_context_tokens"] for item in results),
        "average_tool_calls": statistics.fmean(item["tool_calls"] for item in results),
        "average_core_files": statistics.fmean(len(item["core_files"]) for item in results),
        "average_returned_files": statistics.fmean(len(item["returned_files"]) for item in results),
        "ranking_fallback_rate": statistics.fmean(item["metrics"]["ranking_fallback"] for item in results),
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
        "p99_latency_ms": _percentile(latencies, 99),
    })
    counts["ranking_fallback_rate"] = len(results)
    return metrics, counts


def _aggregate_stage_metrics(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Summarize debug trace timings without mixing them into quality metrics."""
    stages: dict[str, list[float]] = {}
    statuses: dict[str, dict[str, int]] = {}
    for result in results:
        trace = result.get("retrieval_trace", {})
        for name, sample in trace.get("stage_timings", {}).items():
            if not isinstance(sample, dict):
                continue
            value = sample.get("duration_ms")
            if isinstance(value, (int, float)):
                stages.setdefault(name, []).append(float(value))
            status = str(sample.get("status", "unknown"))
            statuses.setdefault(name, {})[status] = statuses.setdefault(name, {}).get(status, 0) + 1
    return {
        name: {
            "samples": len(values),
            "p50_ms": round(_percentile(values, 50), 3),
            "p95_ms": round(_percentile(values, 95), 3),
            "p99_ms": round(_percentile(values, 99), 3),
            "status_counts": statuses.get(name, {}),
        }
        for name, values in sorted(stages.items())
    }


def _category_success(results: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault(result["category"], []).append(result)
    return {
        category: {
            "samples": len(items),
            "success_rate": round(statistics.fmean(1.0 if item["success"] else 0.0 for item in items), 6),
        }
        for category, items in sorted(grouped.items())
    }


def _task_terms(task: str) -> list[str]:
    raw_terms = re.findall(r"[A-Za-z_$][\w$.:/-]{2,}|[\u4e00-\u9fff]{2,}", task.lower())
    terms: list[str] = []
    for term in raw_terms:
        terms.append(term)
        terms.extend(part for part in re.split(r"[.:/-]+", term) if len(part) >= 3)
    return list(dict.fromkeys(term for term in terms if len(term) >= 3))[:24]


def _relevant_excerpt(content: str, task: str, budget: int) -> str:
    if approx_tokens(content) <= budget:
        return content
    lines = content.splitlines()
    terms = _task_terms(task)
    for chinese in re.findall(r"[\u4e00-\u9fff]{2,}", task):
        terms.extend(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
    terms = list(dict.fromkeys(term.lower() for term in terms if len(term) >= 2))[:80]
    ranked: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        score = sum(1 for term in terms if term in lowered)
        if score:
            ranked.append((score, index))
    if not ranked:
        return trim_to_tokens(content, budget)
    selected: set[int] = set()
    for _, index in sorted(ranked, key=lambda item: (-item[0], item[1])):
        selected.update(range(max(0, index - 1), min(len(lines), index + 2)))
        candidate = "\n".join(lines[position] for position in sorted(selected))
        if approx_tokens(candidate) >= budget:
            break
    excerpt = "\n".join(lines[position] for position in sorted(selected))
    return trim_to_tokens(excerpt, budget)


def _contains_text(content: str, expected: str) -> bool:
    normalize = lambda value: re.sub(r"\s+", " ", value).strip().lower()
    return normalize(expected) in normalize(content)


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction
