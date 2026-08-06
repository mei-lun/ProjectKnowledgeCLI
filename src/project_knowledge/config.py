from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .util import atomic_write


DEFAULT_EXCLUDES = [
    ".git/**",
    ".project-kb/**",
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    "**/__pycache__/**",
    ".pytest_cache/**",
    "**/.pytest_cache/**",
    "node_modules/**",
    "dist/**",
    "build/**",
    "target/**",
    "vendor/**",
    "docs/knowledge/generated/**",
]


@dataclass(slots=True)
class ProjectConfig:
    version: int = 1
    project_name: str = "project"
    engine: str = "builtin"
    include: list[str] = field(default_factory=lambda: ["**/*"])
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    knowledge_root: str = "docs/knowledge"
    generated_root: str = "docs/knowledge/generated"
    curated_root: str = "docs/knowledge/curated"
    decisions_root: str = "docs/knowledge/decisions"
    watch: bool = True
    debounce_ms: int = 1000
    generated_mode: str = "auto"
    curated_mode: str = "proposal"
    proposal_trigger: str = "commit"
    max_tokens: int = 6000
    embeddings: str = "disabled"
    local_only: bool = True
    telemetry: bool = False

    @classmethod
    def load(cls, root: Path) -> "ProjectConfig":
        path = root / ".project-kb.yml"
        if not path.exists():
            return cls(project_name=root.name)
        text = path.read_text(encoding="utf-8")
        if text.lstrip().startswith("{"):
            raw = json.loads(text)
        else:
            raw = parse_simple_yaml(text)
        project = raw.get("project", {})
        index = raw.get("index", {})
        knowledge = raw.get("knowledge", {})
        updates = raw.get("updates", {})
        retrieval = raw.get("retrieval", {})
        privacy = raw.get("privacy", {})
        return cls(
            version=int(raw.get("version", 1)),
            project_name=str(project.get("name", root.name)),
            engine=str(index.get("engine", "builtin")),
            include=list(index.get("include", ["**/*"])),
            exclude=list(index.get("exclude", DEFAULT_EXCLUDES)),
            knowledge_root=str(knowledge.get("root", "docs/knowledge")),
            generated_root=str(knowledge.get("generated", "docs/knowledge/generated")),
            curated_root=str(knowledge.get("curated", "docs/knowledge/curated")),
            decisions_root=str(knowledge.get("decisions", "docs/knowledge/decisions")),
            watch=bool(updates.get("watch", True)),
            debounce_ms=int(updates.get("debounce_ms", 1000)),
            generated_mode=str(updates.get("generated_mode", "auto")),
            curated_mode=str(updates.get("curated_mode", "proposal")),
            proposal_trigger=str(updates.get("proposal_trigger", "commit")),
            max_tokens=int(retrieval.get("max_tokens", 6000)),
            embeddings=str(retrieval.get("embeddings", "disabled")),
            local_only=bool(privacy.get("local_only", True)),
            telemetry=bool(privacy.get("telemetry", False)),
        )

    def dump(self) -> str:
        def lines(items: list[str], indent: int) -> list[str]:
            prefix = " " * indent
            return [f"{prefix}- {item}" for item in items]

        output = [
            f"version: {self.version}",
            "",
            "project:",
            f"  name: {self.project_name}",
            "",
            "index:",
            f"  engine: {self.engine}",
            "  include:",
            *lines(self.include, 4),
            "  exclude:",
            *lines(self.exclude, 4),
            "",
            "knowledge:",
            f"  root: {self.knowledge_root}",
            f"  generated: {self.generated_root}",
            f"  curated: {self.curated_root}",
            f"  decisions: {self.decisions_root}",
            "",
            "updates:",
            f"  watch: {str(self.watch).lower()}",
            f"  debounce_ms: {self.debounce_ms}",
            f"  generated_mode: {self.generated_mode}",
            f"  curated_mode: {self.curated_mode}",
            f"  proposal_trigger: {self.proposal_trigger}",
            "",
            "retrieval:",
            f"  max_tokens: {self.max_tokens}",
            f"  embeddings: {self.embeddings}",
            "",
            "privacy:",
            f"  local_only: {str(self.local_only).lower()}",
            f"  telemetry: {str(self.telemetry).lower()}",
            "",
        ]
        return "\n".join(output)

    def write(self, root: Path) -> None:
        atomic_write(root / ".project-kb.yml", self.dump())


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return {}
    if value in {"true", "false"}:
        return value == "true"
    if value in {"null", "~"}:
        return None
    if value.startswith(("'", '"')) and value.endswith(value[0]):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    last_key: tuple[dict[str, Any], str, int] | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if stripped.startswith("- "):
            if last_key is None:
                raise ValueError("list item without a key")
            parent, key, key_indent = last_key
            if indent <= key_indent:
                raise ValueError("invalid list indentation")
            if not isinstance(parent.get(key), list):
                parent[key] = []
            parent[key].append(_scalar(stripped[2:]))
            continue
        key, separator, value = stripped.partition(":")
        if not separator:
            raise ValueError(f"invalid configuration line: {raw_line}")
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        parsed = _scalar(value)
        parent[key] = parsed
        last_key = (parent, key, indent)
        if isinstance(parsed, dict):
            stack.append((indent, parsed))
    return root
