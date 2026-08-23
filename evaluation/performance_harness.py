from __future__ import annotations

import argparse
import json
from pathlib import Path

from project_knowledge.performance import run_performance_harness
from project_knowledge.util import atomic_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Project Knowledge System 性能评测")
    parser.add_argument("--sizes", type=int, nargs="+", default=[500, 5000])
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--enforce-gate", action="store_true", help="return non-zero when P3 performance targets fail")
    args = parser.parse_args()
    report = run_performance_harness(args.sizes, args.repetitions)
    if args.output:
        atomic_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not args.enforce_gate or all(
        item.get("performance_gate", {}).get("passed") is True
        for item in report.get("results", [])
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
