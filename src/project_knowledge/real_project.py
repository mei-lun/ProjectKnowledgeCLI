from __future__ import annotations

import fnmatch
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .config import DEFAULT_EXCLUDES, ProjectConfig
from .engine import BuiltinCodeIndexEngine
from .retrieval import KnowledgeAPI
from .service import ProjectService
from .util import approx_tokens, hash_text, utc_now


REAL_PROJECT_EXCLUDES = [
    ".svn/**",
    "**/.svn/**",
    "**/*.log",
    "**/*.dump",
    "**/*.core",
]


def _matches_pattern(path: str, pattern: str) -> bool:
    return pattern in {"*", "**", "**/*"} or fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch("/" + path, pattern)


def _revision_evidence(root: Path, discovered: list[Any]) -> dict[str, Any]:
    inventory = "\n".join(f"{item.path}:{item.content_hash}" for item in discovered)
    revision = _svn_revision(root)
    if revision:
        return {"mode": "svn", "value": revision, "stable": True}
    return {"mode": "file_hash_only", "value": hash_text(inventory), "stable": True}


def inspect_readonly_scope(source: str | Path, max_files: int | None = None) -> dict[str, Any]:
    source_root = Path(source).resolve()
    if not source_root.is_dir():
        raise ValueError(f"real project source is not a directory: {source_root}")
    if max_files is not None and max_files < 1:
        raise ValueError("max_files must be at least 1")
    config = ProjectConfig(
        project_name=source_root.name,
        exclude=[*DEFAULT_EXCLUDES, *REAL_PROJECT_EXCLUDES],
    )
    engine = BuiltinCodeIndexEngine()
    all_discovered = engine.discover(source_root, config)
    discovered = all_discovered[:max_files] if max_files is not None else all_discovered
    selected_paths = {item.path for item in discovered}
    excluded_files: list[dict[str, str]] = []
    for directory, _, names in os.walk(source_root, topdown=True, followlinks=False):
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_symlink():
                continue
            relative = candidate.relative_to(source_root).as_posix()
            if relative in selected_paths:
                continue
            matches = [
                pattern for pattern in [*DEFAULT_EXCLUDES, *REAL_PROJECT_EXCLUDES]
                if _matches_pattern(relative, pattern)
            ]
            if matches:
                excluded_files.append({"path": relative, "pattern": matches[0]})
    entrypoints = [
        item for item in engine.entrypoints(source_root, config)
        if item["path"] in selected_paths
    ]
    risks: list[dict[str, str]] = []
    if any(item["pattern"].endswith(".svn/**") or ".svn/" in item["path"] for item in excluded_files):
        risks.append({
            "code": "excluded_metadata",
            "severity": "info",
            "message": "已排除 SVN 元数据；revision 使用 SVN 版本或文件哈希回退。",
        })
    if max_files is not None and len(discovered) < len(all_discovered):
        risks.append({
            "code": "file_limit",
            "severity": "high",
            "message": f"仅选择 {len(discovered)}/{len(all_discovered)} 个文件，评测结果不能代表完整项目。",
        })
    if not entrypoints:
        risks.append({
            "code": "no_entrypoints",
            "severity": "high",
            "message": "未检测到 Skynet 启动或协议派发入口，需要人工确认启动方式。",
        })
    revision = _revision_evidence(source_root, discovered)
    if revision["mode"] == "file_hash_only":
        risks.append({
            "code": "revision_fallback",
            "severity": "info",
            "message": "未发现可用 SVN 命令，使用选中文件内容哈希作为 revision。",
        })
    return {
        "source": str(source_root),
        "selected_files": len(discovered),
        "total_discovered_files": len(all_discovered),
        "files": [item.path for item in discovered],
        "excluded_files": sorted(excluded_files, key=lambda item: item["path"]),
        "entrypoints": {
            "count": len(entrypoints),
            "kinds": sorted({str(item["kind"]) for item in entrypoints}),
            "items": entrypoints,
        },
        "revision": revision,
        "risks": risks,
    }


def run_readonly_mirror(source: str | Path, max_files: int | None = None) -> dict[str, Any]:
    source_root = Path(source).resolve()
    if not source_root.is_dir():
        raise ValueError(f"real project source is not a directory: {source_root}")
    scope = inspect_readonly_scope(source_root, max_files)
    config = ProjectConfig(
        project_name=source_root.name,
        exclude=[*DEFAULT_EXCLUDES, *REAL_PROJECT_EXCLUDES],
    )
    engine = BuiltinCodeIndexEngine()
    discovered = engine.discover(source_root, config)
    selected_paths = set(scope["files"])
    discovered = [item for item in discovered if item.path in selected_paths]
    before = _source_snapshot(source_root)
    inventory = "\n".join(f"{item.path}:{item.content_hash}" for item in discovered)
    entrypoint_items = [
        item for item in engine.entrypoints(source_root, config)
        if item["path"] in selected_paths
    ]

    with tempfile.TemporaryDirectory(prefix="project-kb-real-mirror-") as temporary:
        mirror = Path(temporary)
        for item in discovered:
            target = mirror / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / item.path, target)
        config.write(mirror)
        service = ProjectService(mirror)
        started = time.monotonic()
        initialization = service.initialize()
        initialization_ms = (time.monotonic() - started) * 1000
        api = KnowledgeAPI(mirror)
        context_started = time.monotonic()
        context = api.context("Skynet 服务启动、协议派发和数据持久化入口", max_tokens=2000)
        context_ms = (time.monotonic() - context_started) * 1000
        status = service.status()

    after = _source_snapshot(source_root)
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "source": {
            "name": source_root.name,
            "revision": _svn_revision(source_root),
            "revision_mode": scope["revision"]["mode"],
            "revision_value": scope["revision"]["value"],
            "inventory_sha256": hash_text(inventory),
            "discovered_files": len(discovered),
            "unchanged": before == after,
            "excluded_patterns": REAL_PROJECT_EXCLUDES,
            "scope": {
                "total_discovered_files": scope["total_discovered_files"],
                "excluded_files": scope["excluded_files"],
                "risks": scope["risks"],
            },
        },
        "entrypoints": {
            "count": len(entrypoint_items),
            "kinds": sorted({str(item["kind"]) for item in entrypoint_items}),
            "items": entrypoint_items,
        },
        "mirror_initialization": {
            "duration_ms": round(initialization_ms, 3),
            "source_files": len(discovered),
            "indexed_files": initialization["files_scanned"],
            "symbols": initialization["symbols"],
            "relations": initialization["relations"],
            "modules": initialization["modules"],
            "parse_errors": initialization["parse_errors"],
            "parse_success_rate": initialization["parse_success_rate"],
        },
        "probe_context": {
            "duration_ms": round(context_ms, 3),
            "estimated_tokens": approx_tokens(str(context)),
            "returned_files": len(context["impact"]["affected_files"]),
            "returned_symbols": len(context["symbols"]),
            "gaps": context["gaps"],
        },
        "index_health": {
            "content_fresh": status["content_fresh"],
            "commit_aligned": status["commit_aligned"],
            "stale_knowledge": status["counts"]["stale_knowledge"],
            "conflicted_knowledge": status["counts"]["conflicted_knowledge"],
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "cpu_count": os.cpu_count(),
        },
        "limitations": [
            "本报告只证明只读镜像初始化和结构事实索引，不证明业务标准答案正确。",
            "Lua/Skynet 专用语义需要 WP-01/WP-02 Adapter 和 D-007 业务审核。",
        ],
    }


def _source_snapshot(root: Path) -> tuple[tuple[str, str, int, int], ...]:
    entries: list[tuple[str, str, int, int]] = []
    for directory, child_directories, names in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        for name in [*child_directories, *names]:
            path = base / name
            stat = path.stat()
            entries.append((
                path.relative_to(root).as_posix(),
                "directory" if path.is_dir() else "file",
                stat.st_size,
                stat.st_mtime_ns,
            ))
    return tuple(sorted(entries))


def _svn_revision(root: Path) -> str | None:
    executable = shutil.which("svn")
    if not executable:
        return None
    completed = subprocess.run(
        [executable, "info", "--show-item", "revision", str(root)],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout.strip() or None
