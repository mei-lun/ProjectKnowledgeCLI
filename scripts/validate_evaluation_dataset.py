from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def validate_evaluation_dataset(
    datasets: list[str | Path],
    *,
    minimum_questions: int = 300,
    minimum_per_query_type: int = 30,
    minimum_snapshots: int = 3,
) -> tuple[bool, list[str], dict[str, Any]]:
    errors: list[str] = []
    samples: list[dict[str, Any]] = []
    dataset_names: list[str] = []
    for raw_path in datasets:
        path = Path(raw_path)
        dataset_names.append(path.name)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            errors.append(f"dataset cannot be read: {path}: {error}")
            continue
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                errors.append(f"invalid JSON in {path}:{line_number}: {error}")
                continue
            if not isinstance(item, dict):
                errors.append(f"dataset item must be an object: {path}:{line_number}")
                continue
            samples.append(item)

    query_types = Counter(
        str(item.get("query_type") or item.get("category") or item.get("task_type") or "unknown")
        for item in samples
    )
    snapshots = {
        str(
            item.get("snapshot_id")
            or item.get("source_snapshot_sha256")
            or item.get("repository_snapshot")
            or item.get("repository")
            or "unknown"
        )
        for item in samples
    }
    if len(samples) < minimum_questions:
        errors.append(f"dataset has {len(samples)} questions; requires at least {minimum_questions}")
    for query_type, count in sorted(query_types.items()):
        if count < minimum_per_query_type:
            errors.append(
                f"query type {query_type!r} has {count} samples; requires at least {minimum_per_query_type}"
            )
    if len(snapshots - {"unknown"}) < minimum_snapshots:
        errors.append(
            f"dataset has {len(snapshots - {'unknown'})} identified snapshots; requires at least {minimum_snapshots}"
        )
    report = {
        "datasets": dataset_names,
        "questions": len(samples),
        "query_types": dict(sorted(query_types.items())),
        "identified_snapshots": sorted(snapshots - {"unknown"}),
        "minimums": {
            "questions": minimum_questions,
            "per_query_type": minimum_per_query_type,
            "snapshots": minimum_snapshots,
        },
        "passed": not errors,
    }
    return not errors, errors, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the locked multi-snapshot evaluation dataset")
    parser.add_argument("datasets", nargs="+", type=Path)
    parser.add_argument("--minimum-questions", type=int, default=300)
    parser.add_argument("--minimum-per-query-type", type=int, default=30)
    parser.add_argument("--minimum-snapshots", type=int, default=3)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    passed, errors, report = validate_evaluation_dataset(
        args.datasets,
        minimum_questions=args.minimum_questions,
        minimum_per_query_type=args.minimum_per_query_type,
        minimum_snapshots=args.minimum_snapshots,
    )
    payload = {"passed": passed, "errors": errors, "report": report}
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.as_json else ("PASS" if passed else "FAIL"))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
