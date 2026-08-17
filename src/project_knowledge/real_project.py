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

from .codegraph import CodeGraphEngine
from .config import DEFAULT_EXCLUDES, ProjectConfig
from .retrieval import KnowledgeAPI
from .service import ProjectService
from .util import approx_tokens, hash_file, hash_text, utc_now


REAL_PROJECT_EXCLUDES = [
    ".svn/**",
    "**/.svn/**",
    "**/*.log",
    "**/*.dump",
    "**/*.core",
]


def _matches_pattern(path: str, pattern: str) -> bool:
    return pattern in {"*", "**", "**/*"} or fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch("/" + path, pattern)


def _source_files(root: Path, max_files: int | None = None) -> tuple[list[str], list[dict[str, str]]]:
    selected: list[str] = []
    excluded: list[dict[str, str]] = []
    patterns = [*DEFAULT_EXCLUDES, *REAL_PROJECT_EXCLUDES]
    for directory, child_directories, names in os.walk(root, topdown=True, followlinks=False):
        child_directories[:] = [name for name in child_directories if not (Path(directory) / name).is_symlink()]
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_symlink():
                continue
            relative = candidate.relative_to(root).as_posix()
            matches = [pattern for pattern in patterns if _matches_pattern(relative, pattern)]
            if matches:
                excluded.append({"path": relative, "pattern": matches[0]})
            else:
                selected.append(relative)
    selected.sort()
    return (selected[:max_files] if max_files is not None else selected), sorted(excluded, key=lambda item: item["path"])


def _revision_evidence(root: Path, files: list[str]) -> dict[str, Any]:
    revision = _svn_revision(root)
    if revision:
        return {"mode": "svn", "value": revision, "stable": True}
    inventory = "\n".join(f"{path}:{hash_file(root / path)}" for path in files)
    return {"mode": "file_hash_only", "value": hash_text(inventory), "stable": True}


def inspect_readonly_scope(source: str | Path, max_files: int | None = None) -> dict[str, Any]:
    source_root = Path(source).resolve()
    if not source_root.is_dir():
        raise ValueError(f"real project source is not a directory: {source_root}")
    if max_files is not None and max_files < 1:
        raise ValueError("max_files must be at least 1")
    config = ProjectConfig(project_name=source_root.name, exclude=[*DEFAULT_EXCLUDES, *REAL_PROJECT_EXCLUDES])
    snapshot = CodeGraphEngine(config).snapshot(source_root, config)
    files = [item.path for item in snapshot.files]
    if max_files is not None:
        files = files[:max_files]
    _, excluded = _source_files(source_root)
    risks: list[dict[str, str]] = []
    if max_files is not None and len(files) < len(snapshot.files):
        risks.append({"code": "file_limit", "severity": "high", "message": f"仅选择 {len(files)}/{len(snapshot.files)} 个文件，评测结果不能代表完整项目。"})
    revision = _revision_evidence(source_root, files)
    if revision["mode"] == "file_hash_only":
        risks.append({"code": "revision_fallback", "severity": "info", "message": "未发现可用 SVN revision，使用选中文件内容哈希。"})
    return {
        "source": str(source_root),
        "selected_files": len(files),
        "total_discovered_files": len(snapshot.files),
        "files": files,
        "excluded_files": excluded,
        "revision": revision,
        "risks": risks,
        "engine": "codegraph",
    }


def run_readonly_mirror(source: str | Path, max_files: int | None = None) -> dict[str, Any]:
    source_root = Path(source).resolve()
    if not source_root.is_dir():
        raise ValueError(f"real project source is not a directory: {source_root}")
    if max_files is not None and max_files < 1:
        raise ValueError("max_files must be at least 1")
    files, excluded = _source_files(source_root, max_files)
    before = _source_snapshot(source_root)
    revision = _revision_evidence(source_root, files)

    with tempfile.TemporaryDirectory(prefix="project-kb-real-mirror-") as temporary:
        mirror = Path(temporary)
        for relative in files:
            target = mirror / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / relative, target)
        ProjectConfig(project_name=source_root.name, exclude=[*DEFAULT_EXCLUDES, *REAL_PROJECT_EXCLUDES]).write(mirror)
        service = ProjectService(mirror)
        started = time.monotonic()
        initialization = service.initialize()
        initialization_ms = (time.monotonic() - started) * 1000
        api = KnowledgeAPI(mirror)
        context_started = time.monotonic()
        context = api.context("服务启动、协议派发和数据持久化入口", max_tokens=2000)
        context_ms = (time.monotonic() - context_started) * 1000
        status = service.status()

    after = _source_snapshot(source_root)
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "source": {
            "name": source_root.name,
            "revision": _svn_revision(source_root),
            "revision_mode": revision["mode"],
            "revision_value": revision["value"],
            "inventory_sha256": revision["value"],
            "discovered_files": len(files),
            "unchanged": before == after,
            "excluded_patterns": REAL_PROJECT_EXCLUDES,
            "scope": {"total_discovered_files": len(files), "excluded_files": excluded, "risks": []},
        },
        "mirror_initialization": {
            "duration_ms": round(initialization_ms, 3),
            "source_files": len(files),
            "indexed_files": initialization["files_indexed"],
            "engine": "codegraph",
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
            "本报告只证明 CodeGraph 只读镜像初始化，不证明业务标准答案正确。",
            "CodeGraph 公共接口未提供的入口点和路由不会由本地解析器补齐。",
        ],
    }


def _source_snapshot(root: Path) -> tuple[tuple[str, str, int, int], ...]:
    entries: list[tuple[str, str, int, int]] = []
    for directory, child_directories, names in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        for name in [*child_directories, *names]:
            path = base / name
            stat = path.stat()
            entries.append((path.relative_to(root).as_posix(), "directory" if path.is_dir() else "file", stat.st_size, stat.st_mtime_ns))
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
