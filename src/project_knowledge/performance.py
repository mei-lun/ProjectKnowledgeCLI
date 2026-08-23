from __future__ import annotations

import os
import platform
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .retrieval import KnowledgeAPI
from .service import ProjectService
from .util import hash_text, utc_now


FIXTURE_TEMPLATE = '''
def helper_{number}(value):
    return value

def feature_{number}(value):
    return helper_{number}(value)
'''

P3_STAGE_TARGETS_MS = {
    "lexical": 100.0,
    "codegraph": 500.0,
    "ranking": 100.0,
    "context_assembly": 1500.0,
    "context": 1500.0,
}
P3_REQUIRED_STAGES = ("lexical", "codegraph", "ranking", "context_assembly")


def run_performance_harness(sizes: list[int], repetitions: int = 5) -> dict[str, Any]:
    if not sizes or any(size < 1 for size in sizes):
        raise ValueError("performance sizes must contain positive integers")
    if repetitions < 1:
        raise ValueError("performance repetitions must be at least 1")
    results = [_run_size(size, repetitions) for size in sizes]
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "fixture": {
            "layout": "src/benchmark/file_N.py",
            "template_sha256": hash_text(FIXTURE_TEMPLATE),
            "language": "Python",
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "cpu_count": os.cpu_count(),
        },
        "repetitions": repetitions,
        "results": results,
    }


def _run_size(size: int, repetitions: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"project-kb-perf-{size}-") as temporary:
        root = Path(temporary)
        source_root = root / "src" / "benchmark"
        source_root.mkdir(parents=True)
        for number in range(size):
            (source_root / f"file_{number:05}.py").write_text(
                FIXTURE_TEMPLATE.format(number=number), encoding="utf-8"
            )

        service = ProjectService(root)
        started = time.monotonic()
        initialization = service.initialize()
        initialization_ms = (time.monotonic() - started) * 1000
        api = KnowledgeAPI(root)
        status_samples = _measure(repetitions, service.status)
        context_samples, stage_samples = _measure_context(
            repetitions,
            lambda: api.context(f"feature_{size // 2} 如何调用 helper_{size // 2}", max_tokens=1200, debug=True),
        )
        sync_samples = _measure(repetitions, service.sync)

        changed = source_root / "file_00000.py"
        changed.write_text(changed.read_text(encoding="utf-8") + "\n# stale probe\n", encoding="utf-8")
        stale_started = time.monotonic()
        stale_record = api.get("generated.module.benchmark")
        stale_ms = (time.monotonic() - stale_started) * 1000
        stale_passed = (
            stale_record.get("freshness", stale_record.get("status")) == "potentially_stale"
            and "content" not in stale_record
            and "src/benchmark/file_00000.py" in stale_record.get("withheld", "")
        )
        return {
            "file_count": size,
            "indexed_files": initialization["files_indexed"],
            "fact_source": "codegraph",
            "initialization": {"cold_ms": round(initialization_ms, 3)},
            "status": _summary(status_samples),
            "context": _summary(context_samples),
            "stage_metrics": {
                name: _summary(values)
                for name, values in sorted(stage_samples.items())
            },
            "performance_gate": _performance_gate(
                _summary(context_samples),
                {name: _summary(values) for name, values in sorted(stage_samples.items())},
            ),
            "noop_sync": _summary(sync_samples),
            "stale_detection": {"passed": stale_passed, "latency_ms": round(stale_ms, 3)},
        }


def _measure(repetitions: int, operation: Callable[[], Any]) -> list[float]:
    values: list[float] = []
    for _ in range(repetitions):
        started = time.monotonic()
        operation()
        values.append((time.monotonic() - started) * 1000)
    return values


def _measure_context(
    repetitions: int,
    operation: Callable[[], dict[str, Any]],
) -> tuple[list[float], dict[str, list[float]]]:
    values: list[float] = []
    stages: dict[str, list[float]] = {}
    for _ in range(repetitions):
        started = time.monotonic()
        result = operation()
        values.append((time.monotonic() - started) * 1000)
        for name, sample in result.get("retrieval_trace", {}).get("stage_timings", {}).items():
            if isinstance(sample, dict) and isinstance(sample.get("duration_ms"), (int, float)):
                stages.setdefault(name, []).append(float(sample["duration_ms"]))
    return values, stages


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "samples": len(values),
        "average_ms": round(statistics.fmean(values), 3),
        "p50_ms": round(_percentile(values, 50), 3),
        "p95_ms": round(_percentile(values, 95), 3),
        "p99_ms": round(_percentile(values, 99), 3),
        "max_ms": round(max(values), 3),
    }


def _percentile(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _performance_gate(
    context_summary: dict[str, float],
    stage_summaries: dict[str, dict[str, float]],
) -> dict[str, Any]:
    measurements = {"context": context_summary, **stage_summaries}
    failures = [
        {
            "stage": name,
            "metric": "p95_ms",
            "actual_ms": summary.get("p95_ms"),
            "target_ms": P3_STAGE_TARGETS_MS[name],
        }
        for name, summary in measurements.items()
        if name in P3_STAGE_TARGETS_MS and summary.get("p95_ms", 0.0) > P3_STAGE_TARGETS_MS[name]
    ]
    for name in P3_REQUIRED_STAGES:
        summary = stage_summaries.get(name)
        if not isinstance(summary, dict) or not summary.get("samples"):
            failures.append({
                "stage": name,
                "reason": "missing_samples",
                "target_ms": P3_STAGE_TARGETS_MS[name],
            })
    return {
        "schema_version": 1,
        "passed": not failures,
        "targets_ms": dict(P3_STAGE_TARGETS_MS),
        "failures": failures,
        "context_p95_ms": context_summary.get("p95_ms", 0.0),
    }
