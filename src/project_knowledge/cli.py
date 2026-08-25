from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import ProjectConfig
from .evidence import EvidencePackBuilder
from .errors import ProjectKnowledgeError
from .evaluate import STRATEGIES, evaluate_suite, load_json_object
from .finalization import FinalizationService
from .mcp import serve
from .models import PatchOperation
from .proposal import ProposalService
from .provider import ModelRuntime, ProviderConfig, create_preview_provider, create_provider
from .progress import TerminalProgressRenderer
from .schemas import FEATURE_GUIDE_DRAFT_SCHEMA
from .semantic import SemanticKnowledgeService
from .service import ProjectService
from .util import atomic_json


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
        ("migrate", "migrate project configuration forward"),
    ]:
        sub = commands.add_parser(name, help=help_text)
        _common(sub, dry_run=name in {"init", "sync", "rebuild", "install", "uninstall"})
        if name == "sync":
            sub.add_argument("--task-summary", default="manual synchronization")
        if name in {"install", "uninstall"}:
            sub.add_argument("--client", action="append", choices=["claude", "cursor", "gemini"], dest="clients")

    watch = commands.add_parser("watch", help="watch for file changes and synchronize")
    _common(watch, dry_run=True)
    watch.add_argument("--once", action="store_true", help="perform one polling cycle and exit")

    finalization = commands.add_parser("finalize", help="synchronize and verify release knowledge alignment")
    _common(finalization, dry_run=False)
    finalization.add_argument("--check", action="store_true", help="verify readiness without writing files")

    mcp = commands.add_parser("mcp", help="run the stdio MCP server and guidance workflow")
    mcp.add_argument("--project", default=".", help="initialized project path")

    git_event = commands.add_parser("git-event", help="record and compensate a Git lifecycle event")
    git_event.add_argument("--event", required=True, choices=["post-checkout", "post-merge", "post-rewrite", "post-commit"])
    git_event.add_argument("--project", default=".", help="project path")
    git_event.add_argument("--old-head", default="")
    git_event.add_argument("--new-head", default="")
    git_event.add_argument("--flag", default="")
    git_event.add_argument("--json", action="store_true", dest="as_json")
    git_event.add_argument("--quiet", action="store_true")

    evaluation = commands.add_parser("evaluate", help="evaluate retrieval against a JSONL dataset")
    evaluation.add_argument("dataset", help="JSONL dataset with task and expected source anchors")
    evaluation.add_argument("--project", default=".", help="initialized project path")
    evaluation.add_argument(
        "--strategy", action="append", choices=[*sorted(STRATEGIES), "all"],
        help="可重复指定评测策略；all 运行全部策略",
    )
    evaluation.add_argument("--thresholds", help="质量阈值 JSON")
    evaluation.add_argument("--baseline", help="用于回归比较的历史评测 JSON")
    evaluation.add_argument("--output", help="原子写入机器可读评测报告")
    evaluation.add_argument("--limit", type=int, help="仅运行前 N 条样本")
    evaluation.add_argument("--json", action="store_true", dest="as_json")
    evaluation.add_argument("--quiet", action="store_true")

    generation = commands.add_parser("generate", help="预览或执行基于有限证据包的结构化语义生成")
    generation.add_argument("task", help="中文功能开发任务或语义生成目标")
    generation.add_argument("--project", default=".", help="已初始化或待分析的项目路径")
    generation.add_argument("--file", action="append", required=True, dest="files", help="项目内相对证据文件；可重复")
    generation.add_argument("--dry-run", action="store_true", help="仅预览字段、文件、Token 和脱敏统计，不调用 Provider")
    generation.add_argument("--save-draft", action="store_true", help="校验成功后保存并索引 Feature Guide 草案")
    generation.add_argument("--json", action="store_true", dest="as_json")
    generation.add_argument("--quiet", action="store_true")

    candidates = commands.add_parser("feature-candidates", help="从代码索引列出可进一步语义生成的功能域候选")
    candidates.add_argument("--project", default=".", help="已初始化项目路径")
    candidates.add_argument("--limit", type=int, default=50, help="最多返回的候选数量")
    candidates.add_argument("--json", action="store_true", dest="as_json")
    candidates.add_argument("--quiet", action="store_true")

    propose = commands.add_parser("propose", help="创建可审核的知识更新提案")
    propose.add_argument("range", nargs="?", default="HEAD", help="提案对应的 Git 范围或变更标识")
    propose.add_argument("--project", default=".", help="已初始化项目路径")
    propose.add_argument("--draft", help="将已校验的 Feature Guide 草案 ID 转为审核提案")
    propose.add_argument("--target", help="curated 文档或新 ADR 草案的项目内路径")
    propose.add_argument("--reason", help="本次知识变更理由")
    propose.add_argument("--evidence", action="append", help="来源文件、符号或决策；可重复")
    propose.add_argument(
        "--operation",
        choices=["upsert_generated_block", "delete_generated_block", "append_adr_draft"],
        help="结构化 Patch operation",
    )
    propose.add_argument("--block-id", help="generated block 的稳定 ID")
    content = propose.add_mutually_exclusive_group()
    content.add_argument("--content", help="待应用的中文 Markdown 内容")
    content.add_argument("--content-file", help="从 UTF-8 文件读取待应用内容")
    propose.add_argument("--supersedes", action="append", default=[], help="删除或 ADR 替代的知识 ID；可重复")
    propose.add_argument("--deleted-source", action="append", default=[], help="已删除的来源路径或符号；可重复")
    propose.add_argument("--confidence", type=float, default=0.8)
    propose.add_argument("--dry-run", action="store_true")
    propose.add_argument("--json", action="store_true", dest="as_json")
    propose.add_argument("--quiet", action="store_true")

    for name, help_text in [("apply", "应用已审核提案"), ("reject", "拒绝提案并保留审计记录")]:
        review = commands.add_parser(name, help=help_text)
        review.add_argument("proposal_id")
        review.add_argument("--project", default=".", help="已初始化项目路径")
        review.add_argument("--reviewer", required=True, help="审核人")
        review.add_argument("--reason", required=True, help="审核理由")
        review.add_argument("--dry-run", action="store_true")
        review.add_argument("--json", action="store_true", dest="as_json")
        review.add_argument("--quiet", action="store_true")
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
        if args.command == "generate":
            root = Path(args.project).resolve()
            project_config = ProjectConfig.load(root)
            provider_config = ProviderConfig.from_project_config(project_config)
            pack = EvidencePackBuilder(
                root,
                max_files=provider_config.max_files,
                max_tokens=provider_config.max_tokens,
            ).build(args.task, args.files)
            provider = create_preview_provider(provider_config) if args.dry_run else create_provider(provider_config)
            runtime = ModelRuntime(root, provider, provider_config)
            if args.dry_run and args.save_draft:
                raise ValueError("--dry-run 与 --save-draft 不能同时使用")
            if args.dry_run:
                result = runtime.preview(pack, FEATURE_GUIDE_DRAFT_SCHEMA)
            elif args.save_draft:
                result = SemanticKnowledgeService(root, runtime).generate_feature_guide(pack, persist=True)
            else:
                result = runtime.generate(pack, FEATURE_GUIDE_DRAFT_SCHEMA).to_dict()
            exit_code = 0
        elif args.command == "git-event":
            result = ProjectService(args.project).git_event(
                args.event, old_head=args.old_head, new_head=args.new_head, flag=args.flag,
            )
            exit_code = 0
        elif args.command == "feature-candidates":
            result = {
                "project": str(Path(args.project).resolve()),
                "candidates": SemanticKnowledgeService(args.project).discover_feature_candidates(args.limit),
            }
            exit_code = 0
        elif args.command == "propose":
            proposal_service = ProposalService(args.project)
            if args.draft:
                explicit = [
                    args.target, args.reason, args.evidence, args.operation, args.block_id,
                    args.content, args.content_file, args.supersedes, args.deleted_source,
                ]
                if any(value for value in explicit):
                    raise ValueError("--draft 不能与手工 target/operation/content 参数同时使用")
                created = proposal_service.create_from_feature_draft(
                    args.draft, change_range=args.range, dry_run=args.dry_run,
                )
            else:
                missing = [
                    name for name, value in [
                        ("--target", args.target), ("--reason", args.reason),
                        ("--evidence", args.evidence), ("--operation", args.operation),
                    ] if not value
                ]
                if missing:
                    raise ValueError("手工提案缺少参数：" + "、".join(missing))
                if args.content_file:
                    operation_content = Path(args.content_file).read_text(encoding="utf-8")
                else:
                    operation_content = args.content
                operation = PatchOperation(
                    op=args.operation, content=operation_content,
                    block_id=args.block_id, supersedes=args.supersedes,
                    deleted_sources=args.deleted_source,
                )
                created = proposal_service.create(
                    target=args.target, reason=args.reason, evidence=args.evidence,
                    operations=[operation], confidence=args.confidence,
                    change_range=args.range, dry_run=args.dry_run,
                )
            result = created if isinstance(created, dict) else {**created.to_dict(), "action": "propose", "dry_run": False}
            exit_code = 0
        elif args.command == "apply":
            result = ProposalService(args.project).apply(
                args.proposal_id, reviewer=args.reviewer,
                review_reason=args.reason, dry_run=args.dry_run,
            )
            exit_code = 0
        elif args.command == "reject":
            result = ProposalService(args.project).reject(
                args.proposal_id, reviewer=args.reviewer,
                review_reason=args.reason, dry_run=args.dry_run,
            )
            exit_code = 0
        elif args.command == "evaluate":
            selected = args.strategy or ["hybrid"]
            if "all" in selected:
                selected = ["hybrid", "grep_read", "code", "markdown", "codegraph"]
            thresholds = load_json_object(args.thresholds) if args.thresholds else None
            if args.baseline and thresholds is None:
                raise ValueError("--baseline requires --thresholds")
            baseline = load_json_object(args.baseline) if args.baseline else None
            result = evaluate_suite(
                args.project, args.dataset, strategies=selected,
                thresholds=thresholds, baseline=baseline, limit=args.limit,
            )
            if args.output:
                atomic_json(Path(args.output), result)
            gate = result["quality_gate"]
            exit_code = 2 if gate["evaluated"] and not gate["passed"] else 0
        elif args.command == "finalize":
            result, ready = FinalizationService(args.path).finalize(check_only=args.check)
            exit_code = 0 if ready else 2
        else:
            service = ProjectService(args.path)
            if args.command == "init":
                renderer = None
                if not args.as_json and not args.quiet and sys.stderr.isatty():
                    renderer = TerminalProgressRenderer(sys.stderr)
                try:
                    result = service.initialize(args.dry_run, progress=renderer)
                finally:
                    if renderer is not None:
                        renderer.close()
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
            elif args.command == "migrate":
                result = service.migrate(args.dry_run)
            elif args.command == "watch":
                if args.dry_run:
                    result = service.sync(dry_run=True, task_summary="watcher dry run")
                    result["action"] = "watch"
                else:
                    service.watch(args.once)
                    result = {"action": "watch", "completed": args.once}
            elif args.command == "install":
                result = service.install(args.dry_run, args.clients)
            elif args.command == "uninstall":
                result = service.uninstall(args.dry_run, args.clients)
            elif args.command == "doctor":
                result = service.doctor()
            else:
                raise RuntimeError(f"unsupported command: {args.command}")
            exit_code = 0
    except ProjectKnowledgeError as error:
        if getattr(args, "as_json", False):
            print(json.dumps(error.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"project-kb: {error}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"project-kb: {error}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2) if getattr(args, "as_json", False) else _human(result))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
