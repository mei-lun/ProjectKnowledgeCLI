from __future__ import annotations

import argparse
import json
from pathlib import Path

from project_knowledge.real_project import inspect_readonly_scope, run_readonly_mirror
from project_knowledge.util import atomic_json


def main() -> int:
    parser = argparse.ArgumentParser(description="在临时镜像中评测真实项目，绝不写入源目录")
    parser.add_argument("source", help="真实项目只读源目录")
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--dry-run", action="store_true", help="仅输出范围、排除项、风险和 revision，不创建镜像")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = inspect_readonly_scope(args.source, args.max_files) if args.dry_run else run_readonly_mirror(args.source, args.max_files)
    if args.output:
        atomic_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if args.dry_run or report["source"]["unchanged"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
