from __future__ import annotations

import json
import os
import platform
import re
import statistics
import time
from pathlib import Path
from typing import Any, Iterable

from .retrieval import KnowledgeAPI
from .store import KnowledgeStore
from .util import approx_tokens, hash_file, read_text, trim_to_tokens, utc_now


SCHEMA_VERSION = 1
STRATEGIES = {"hybrid", "grep_read", "code", "markdown", "codegraph"}
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
        if sample["schema_version"] != SCHEMA_VERSION:
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
        if not any(sample.get(field) for field in EXPECTED_LIST_FIELDS) and "expected_stale" not in sample:
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
    if strategy == "codegraph":
        return {
            "schema_version": SCHEMA_VERSION,
            "strategy": strategy,
            "available": False,
            "reason_code": "adapter_unavailable",
            "message": "真实 CodeGraph Adapter 尚未实现；评测不会用 builtin 伪造 codegraph 结果。",
        }

    dataset_path = Path(dataset)
    samples = load_dataset(dataset_path)
    if limit is not None:
        if limit < 1:
            raise ValueError("evaluation limit must be at least 1")
        samples = samples[:limit]
    api = KnowledgeAPI(project)
    results = [_evaluate_sample(api, sample, strategy) for sample in samples]
    status = api.status()
    metrics, metric_counts = _aggregate(results)
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
        "category_success": _category_success(results),
        "failure_samples": [item["id"] for item in results if not item["success"]],
        "reproducibility": {
            "generated_at": utc_now(),
            "dataset": dataset_path.name,
            "dataset_sha256": hash_file(dataset_path),
            "project": api.config.project_name,
            "head_commit": status.get("head_commit"),
            "index_commit": status.get("index_commit"),
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
                    if metric in maximum_metrics
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

    if "expected_stale" in sample:
        stale_match = bool(returned["stale_detected"]) == sample["expected_stale"]
        metrics["stale_detection"] = 1.0 if stale_match else 0.0
        if not stale_match:
            failures.append("stale_detection")

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
        "estimated_context_tokens": context_tokens,
        "tool_calls": returned["tool_calls"],
        "latency_ms": round(latency_ms, 3),
        "returned_files": sorted(returned["files"]),
        "returned_symbols": sorted(returned["symbols"]),
        "returned_call_path": sorted(returned["call_path"]),
        "stale_detected": returned["stale_detected"],
    }


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


def _retrieve(api: KnowledgeAPI, sample: dict[str, Any], strategy: str) -> dict[str, Any]:
    task = sample["task"]
    budget = sample.get("max_tokens", 4000)
    if strategy in {"hybrid", "code"}:
        context = api.context(task, budget)
        symbols = {item["id"] for item in context["symbols"]}
        files = {item["path"] for item in context["symbols"]}
        files.update(context["impact"]["affected_files"])
        text_parts: list[str] = []
        stale_detected = False
        if strategy == "hybrid":
            for record in context["knowledge"]:
                files.update(source.get("path") for source in record["sources"] if source.get("path"))
                symbols.update(source.get("id") for source in record["sources"] if source.get("id"))
                text_parts.append(record.get("content", ""))
                stale_detected = stale_detected or bool(record.get("requires_live_source"))
        impact = api.impact(symbols=sorted(symbols)) if symbols else {"relations": [], "affected_files": []}
        files.update(impact.get("affected_files", []))
        call_path = set(symbols)
        for relation in impact.get("relations", []):
            call_path.add(relation["source"])
            if relation.get("resolved"):
                call_path.add(relation["target"])
        compact = {"symbols": sorted(symbols), "files": sorted(files)}
        if strategy == "hybrid":
            compact["summary"] = context.get("summary")
        text_parts.append(json.dumps(compact, ensure_ascii=False, separators=(",", ":")))
        return {
            "files": files, "symbols": symbols, "call_path": call_path,
            "text": "\n".join(text_parts), "stale_detected": stale_detected,
            "tool_calls": 4 if strategy == "hybrid" else 3,
        }

    if strategy == "markdown":
        search = api.search(task, limit=10)
        files: set[str] = set()
        symbols: set[str] = set()
        text_parts: list[str] = []
        stale_detected = False
        reads = 0
        remaining = budget
        for item in _select_markdown_pages(search["results"], limit=3):
            if remaining <= 0:
                break
            reads += 1
            record = api.get(item["id"])
            files.update(source.get("path") for source in record["sources"] if source.get("path"))
            symbols.update(source.get("id") for source in record["sources"] if source.get("id"))
            content = record.get("content", item.get("summary", ""))
            content = _relevant_excerpt(content, task, remaining)
            while content and approx_tokens(content) > remaining:
                content = content[:-1]
            if content:
                text_parts.append(content)
                remaining -= approx_tokens(content)
            stale_detected = stale_detected or bool(record.get("requires_live_source"))
        return {
            "files": files, "symbols": symbols, "call_path": set(symbols),
            "text": "\n".join(text_parts), "stale_detected": stale_detected,
            "tool_calls": 1 + reads,
        }

    terms = _task_terms(task)
    ranked: list[tuple[int, str, str]] = []
    for item in api.service.engine.discover(api.root, api.config):
        content = read_text(api.root / item.path)
        lowered = content.lower()
        score = sum(lowered.count(term) for term in terms)
        if score:
            ranked.append((score, item.path, content))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = ranked[:7]
    return {
        "files": {item[1] for item in selected}, "symbols": set(), "call_path": set(),
        "text": "\n".join(item[2][:4000] for item in selected), "stale_detected": False,
        "tool_calls": 1 + len(selected),
    }


def _aggregate(results: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, int]]:
    names = set().union(*(item["metrics"].keys() for item in results))
    metrics: dict[str, float] = {}
    counts: dict[str, int] = {}
    for name in sorted(names):
        values = [item["metrics"][name] for item in results if name in item["metrics"]]
        metrics[name] = statistics.fmean(values)
        counts[name] = len(values)
    latencies = [item["latency_ms"] for item in results]
    metrics.update({
        "success_rate": statistics.fmean(1.0 if item["success"] else 0.0 for item in results),
        "average_context_tokens": statistics.fmean(item["estimated_context_tokens"] for item in results),
        "average_tool_calls": statistics.fmean(item["tool_calls"] for item in results),
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
        "p99_latency_ms": _percentile(latencies, 99),
    })
    return metrics, counts


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
