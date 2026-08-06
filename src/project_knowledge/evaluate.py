from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .retrieval import KnowledgeAPI
from .util import approx_tokens


def evaluate(project: str | Path, dataset: str | Path) -> dict[str, Any]:
    api = KnowledgeAPI(project)
    samples: list[dict[str, Any]] = []
    for number, line in enumerate(Path(dataset).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        sample = json.loads(line)
        context = api.context(sample["task"], sample.get("max_tokens", 4000))
        returned_files = {item["path"] for item in context["symbols"]}
        returned_files.update(context["impact"]["affected_files"])
        for record in context["knowledge"]:
            returned_files.update(source.get("path") for source in record["sources"] if source.get("path"))
        returned_symbols = {item["id"] for item in context["symbols"]}
        expected_files = set(sample.get("expected_files", []))
        expected_symbols = set(sample.get("expected_symbols", []))
        file_recall = len(expected_files & returned_files) / max(1, len(expected_files))
        symbol_recall = len(expected_symbols & returned_symbols) / max(1, len(expected_symbols))
        samples.append({
            "line": number, "task": sample["task"], "file_recall": file_recall,
            "file_precision": len(expected_files & returned_files) / max(1, len(returned_files)),
            "symbol_recall": symbol_recall,
            "symbol_precision": len(expected_symbols & returned_symbols) / max(1, len(returned_symbols)),
            "estimated_tokens": approx_tokens(json.dumps(context, ensure_ascii=False)),
            "returned_files": sorted(returned_files), "returned_symbols": sorted(returned_symbols),
        })
    return {
        "samples": len(samples),
        "file_recall": sum(item["file_recall"] for item in samples) / max(1, len(samples)),
        "file_precision": sum(item["file_precision"] for item in samples) / max(1, len(samples)),
        "symbol_recall": sum(item["symbol_recall"] for item in samples) / max(1, len(samples)),
        "symbol_precision": sum(item["symbol_precision"] for item in samples) / max(1, len(samples)),
        "average_context_tokens": sum(item["estimated_tokens"] for item in samples) / max(1, len(samples)),
        "results": samples,
    }
