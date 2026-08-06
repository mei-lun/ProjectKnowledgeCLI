from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .evaluate import evaluate
from .mcp import serve
from .service import ProjectService


def _common(parser: argparse.ArgumentParser, path: bool = True, dry_run: bool = True) -> None:
    if path:
        parser.add_argument("path", nargs="?", default=".", help="project path (default: current directory)")
    if dry_run:
        parser.add_argument("--dry-run", action="store_true", help="show intended writes without changing files")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable JSON")
    parser.add_argument("--quiet", action="store_true", help="suppress successful output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project-kb", description="Local-first project knowledge system")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    for name, help_text in [
        ("init", "initialize and fully index a project"),
        ("sync", "incrementally synchronize changed files"),
        ("rebuild", "atomically rebuild the local index"),
        ("status", "show index and knowledge health"),
        ("check", "run CI-oriented health checks"),
        ("install", "install owned client integration markers"),
        ("uninstall", "remove owned integration markers and preserve knowledge"),
        ("doctor", "inspect runtime and project setup"),
    ]:
        sub = commands.add_parser(name, help=help_text)
        _common(sub, dry_run=name in {"init", "sync", "rebuild", "install", "uninstall"})
        if name == "sync":
            sub.add_argument("--task-summary", default="manual synchronization")

    watch = commands.add_parser("watch", help="watch for file changes and synchronize")
    _common(watch, dry_run=True)
    watch.add_argument("--once", action="store_true", help="perform one polling cycle and exit")

    mcp = commands.add_parser("mcp", help="run the read-only stdio MCP server")
    mcp.add_argument("--project", default=".", help="initialized project path")

    evaluation = commands.add_parser("evaluate", help="evaluate retrieval against a JSONL dataset")
    evaluation.add_argument("dataset", help="JSONL dataset with task and expected source anchors")
    evaluation.add_argument("--project", default=".", help="initialized project path")
    evaluation.add_argument("--json", action="store_true", dest="as_json")
    evaluation.add_argument("--quiet", action="store_true")
    return parser


def _human(value: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, item in value.items():
        label = key.replace("_", " ").title()
        if isinstance(item, (dict, list)):
            lines.append(f"{label}: {json.dumps(item, ensure_ascii=False)}")
        else:
            lines.append(f"{label}: {item}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "mcp":
        serve(args.project)
        return 0
    try:
        if args.command == "evaluate":
            result = evaluate(args.project, args.dataset)
            exit_code = 0
        else:
            service = ProjectService(args.path)
            if args.command == "init":
                result = service.initialize(args.dry_run)
            elif args.command == "sync":
                result = service.sync(args.dry_run, args.task_summary)
            elif args.command == "rebuild":
                result = service.rebuild(args.dry_run)
            elif args.command == "status":
                result = service.status()
            elif args.command == "check":
                result, healthy = service.check()
                exit_code = 0 if healthy else 2
                if not args.quiet:
                    print(json.dumps(result, ensure_ascii=False, indent=2) if args.as_json else _human(result))
                return exit_code
            elif args.command == "watch":
                if args.dry_run:
                    result = service.sync(dry_run=True, task_summary="watcher dry run")
                    result["action"] = "watch"
                else:
                    service.watch(args.once)
                    result = {"action": "watch", "completed": args.once}
            elif args.command == "install":
                result = service.install(args.dry_run)
            elif args.command == "uninstall":
                result = service.uninstall(args.dry_run)
            elif args.command == "doctor":
                result = service.doctor()
            else:
                raise RuntimeError(f"unsupported command: {args.command}")
            exit_code = 0
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"project-kb: {error}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2) if getattr(args, "as_json", False) else _human(result))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
