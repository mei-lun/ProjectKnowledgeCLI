from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schemas import CONFIG_SCHEMA, validate_instance
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
    "docs/knowledge/drafts/**",
    "evaluation/reports/**",
    "evaluation/baselines/**",
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
    drafts_root: str = "docs/knowledge/drafts"
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
    provider_id: str = "disabled"
    provider_model: str = ""
    provider_endpoint: str = ""
    provider_enabled: bool = False
    provider_allow_network: bool = False
    provider_authorization: str = ""
    provider_api_key_env: str = ""
    provider_timeout_seconds: int = 30
    provider_max_retries: int = 2
    provider_cache: bool = True
    provider_checkpoint: bool = True
    provider_max_files: int = 20
    provider_max_tokens: int = 12000
    provider_prompt_version: str = "feature-guide-v1"
    provider_output_schema_version: str = "semantic-draft-v1"

    @staticmethod
    def load_raw(root: Path) -> dict[str, Any]:
        path = root / ".project-kb.yml"
        if not path.exists():
            return {"version": 1}
        text = path.read_text(encoding="utf-8")
        value = json.loads(text) if text.lstrip().startswith("{") else parse_simple_yaml(text)
        if not isinstance(value, dict):
            raise ValueError(".project-kb.yml 必须是对象")
        return value

    @classmethod
    def validate_file(cls, root: Path) -> dict[str, Any]:
        raw = cls.load_raw(root)
        validate_instance(raw, CONFIG_SCHEMA)
        return raw

    @classmethod
    def migrate_file(cls, root: Path, dry_run: bool = False) -> dict[str, Any]:
        path = root / ".project-kb.yml"
        if not path.exists():
            raise FileNotFoundError(f"{path} 不存在")
        text = path.read_text(encoding="utf-8")
        raw = cls.load_raw(root)
        version = int(raw.get("version", 1))
        if version > 1:
            raise ValueError(f"不支持配置版本 {version}，当前最高版本为 1")
        if version >= 1:
            validate_instance(raw, CONFIG_SCHEMA)
            return {"action": "migrate", "changed": False, "version": version, "path": str(path)}
        if text.lstrip().startswith("{"):
            migrated = dict(raw)
            migrated["version"] = 1
            rendered = json.dumps(migrated, ensure_ascii=False, indent=2) + "\n"
        else:
            lines = text.splitlines(keepends=True)
            replaced = False
            for index, line in enumerate(lines):
                if line.lstrip().startswith("version:"):
                    newline = "\n" if line.endswith("\n") else ""
                    lines[index] = "version: 1" + newline
                    replaced = True
                    break
            rendered = "".join(lines) if replaced else "version: 1\n" + text
        result = {
            "action": "migrate", "changed": True, "from_version": version,
            "to_version": 1, "path": str(path), "preserved_user_fields": sorted(
                key for key in raw if key not in {"version", "project", "index", "knowledge", "updates", "retrieval", "privacy", "provider"}
            ),
        }
        if dry_run:
            result["dry_run"] = True
            result["content_hash"] = __import__("hashlib").sha256(rendered.encode("utf-8")).hexdigest()
            return result
        atomic_write(path, rendered)
        validate_instance(cls.load_raw(root), CONFIG_SCHEMA)
        return result

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
        provider = raw.get("provider", {})
        return cls(
            version=int(raw.get("version", 1)),
            project_name=str(project.get("name", root.name)),
            engine=str(index.get("engine", "builtin")),
            include=list(index.get("include", ["**/*"])),
            exclude=list(index.get("exclude", DEFAULT_EXCLUDES)),
            knowledge_root=str(knowledge.get("root", "docs/knowledge")),
            generated_root=str(knowledge.get("generated", "docs/knowledge/generated")),
            drafts_root=str(knowledge.get("drafts", "docs/knowledge/drafts")),
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
            provider_id=str(provider.get("id", "disabled")),
            provider_model=str(provider.get("model", "")),
            provider_endpoint=str(provider.get("endpoint", "")),
            provider_enabled=bool(provider.get("enabled", False)),
            provider_allow_network=bool(provider.get("allow_network", False)),
            provider_authorization=str(provider.get("authorization", "")),
            provider_api_key_env=str(provider.get("api_key_env", "")),
            provider_timeout_seconds=int(provider.get("timeout_seconds", 30)),
            provider_max_retries=int(provider.get("max_retries", 2)),
            provider_cache=bool(provider.get("cache", True)),
            provider_checkpoint=bool(provider.get("checkpoint", True)),
            provider_max_files=int(provider.get("max_files", 20)),
            provider_max_tokens=int(provider.get("max_tokens", 12000)),
            provider_prompt_version=str(provider.get("prompt_version", "feature-guide-v1")),
            provider_output_schema_version=str(provider.get("output_schema_version", "semantic-draft-v1")),
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
            f"  drafts: {self.drafts_root}",
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
            "provider:",
            f"  id: {self.provider_id}",
            f"  model: {json.dumps(self.provider_model, ensure_ascii=False)}",
            f"  endpoint: {json.dumps(self.provider_endpoint, ensure_ascii=False)}",
            f"  enabled: {str(self.provider_enabled).lower()}",
            f"  allow_network: {str(self.provider_allow_network).lower()}",
            f"  authorization: {json.dumps(self.provider_authorization, ensure_ascii=False)}",
            f"  api_key_env: {json.dumps(self.provider_api_key_env, ensure_ascii=False)}",
            f"  timeout_seconds: {self.provider_timeout_seconds}",
            f"  max_retries: {self.provider_max_retries}",
            f"  cache: {str(self.provider_cache).lower()}",
            f"  checkpoint: {str(self.provider_checkpoint).lower()}",
            f"  max_files: {self.provider_max_files}",
            f"  max_tokens: {self.provider_max_tokens}",
            f"  prompt_version: {self.provider_prompt_version}",
            f"  output_schema_version: {self.provider_output_schema_version}",
            "",
        ]
        return "\n".join(output)

    def write(self, root: Path) -> None:
        atomic_write(root / ".project-kb.yml", self.dump())

    def capability_warnings(self) -> list[dict[str, str]]:
        """Return explicit warnings for configured behavior that is not wired yet."""
        warnings: list[dict[str, str]] = []
        conditional = [
            (
                self.version != 1,
                "version", str(self.version), "unsupported_config_version",
                "仅支持配置版本 1；配置迁移将在后续工作包实现。",
            ),
            (
                self.generated_mode != "auto",
                "updates.generated_mode", self.generated_mode, "unsupported_generated_mode",
                "仅支持 generated_mode: auto。",
            ),
            (
                self.embeddings != "disabled",
                "retrieval.embeddings", self.embeddings, "unsupported_embeddings",
                "向量检索尚未实现；当前仅支持 embeddings: disabled。",
            ),
            (
                not self.local_only,
                "privacy.local_only", str(self.local_only).lower(), "external_transfer_policy_relaxed",
                "local_only 已关闭；该设置本身不会发起网络请求，HTTP Provider 仍需 enabled、allow_network 和外发授权。",
            ),
            (
                self.telemetry,
                "privacy.telemetry", str(self.telemetry).lower(), "unsupported_telemetry",
                "遥测尚未实现；telemetry: true 不会发送任何数据。",
            ),
        ]
        for enabled, field_name, value, code, message in conditional:
            if enabled:
                warnings.append({"field": field_name, "value": value, "code": code, "message": message})
        provider_warnings = [
            (
                self.provider_id not in {"disabled", "fake", "http-json"},
                "provider.id", self.provider_id, "unsupported_provider",
                "仅支持 disabled、fake 和 http-json Provider。",
            ),
            (
                self.provider_id != "disabled" and not self.provider_enabled,
                "provider.enabled", str(self.provider_enabled).lower(), "provider_not_enabled",
                "Provider 已选择但未显式启用，不会执行模型请求。",
            ),
            (
                self.provider_id == "http-json" and not self.provider_allow_network,
                "provider.allow_network", str(self.provider_allow_network).lower(), "provider_network_not_authorized",
                "HTTP Provider 未获网络授权，不会发送证据。",
            ),
        ]
        for enabled, field_name, value, code, message in provider_warnings:
            if enabled:
                warnings.append({"field": field_name, "value": value, "code": code, "message": message})
        return warnings


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
