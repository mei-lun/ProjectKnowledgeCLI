from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import ProjectConfig
from .models import Relation, Symbol


@dataclass(slots=True, frozen=True)
class IndexedFile:
    path: str
    language: str
    module: str
    size: int
    mtime_ns: int
    content_hash: str


@dataclass(slots=True, frozen=True)
class CodeIndexSnapshot:
    snapshot_id: str
    files: tuple[IndexedFile, ...]


class CodeIndexEngine(Protocol):
    def snapshot(self, root: Path, config: ProjectConfig) -> CodeIndexSnapshot: ...
    def status(self) -> dict[str, object]: ...
    def diagnose(self, root: Path) -> dict[str, object]: ...
    def initialize(self, root: Path, config: ProjectConfig) -> dict[str, object]: ...
    def sync(self, root: Path, config: ProjectConfig, previous: dict[str, str] | None = None) -> dict[str, object]: ...
    def search_symbols(self, root: Path, config: ProjectConfig, query: str, limit: int = 20) -> list[Symbol]: ...
    def get_source(self, root: Path, path: str, start_line: int | None = None, end_line: int | None = None) -> str: ...
    def trace(self, root: Path, symbol_id: str, config: ProjectConfig, max_depth: int = 1, limit: int = 200) -> list[Relation]: ...
    def impact(self, root: Path, config: ProjectConfig, files: list[str] | None = None, symbols: list[str] | None = None, max_hops: int = 1, max_relations: int = 500) -> dict[str, object]: ...
    def affected_tests(self, root: Path, config: ProjectConfig, files: list[str]) -> list[str]: ...


def _module_for(path: str) -> str:
    parts = Path(path).parts
    if not parts:
        return "root"
    if parts[0] in {"src", "lib", "app", "packages", "services", "cmd"} and len(parts) > 1:
        return parts[1]
    if parts[0] in {"dev", "skynet", "service", "config"} and len(parts) > 1:
        return f"{parts[0]}/{parts[1]}"
    return parts[0] if len(parts) > 1 else "root"


def create_engine(config: ProjectConfig) -> CodeIndexEngine:
    from .codegraph import CodeGraphEngine

    return CodeGraphEngine(config)
