from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .codegraph import CodeGraphClient, CodeGraphError
from .util import hash_file, read_text


CATEGORIES = {
    "activity-development": "普通活动开发",
    "player-feature-development": "普通玩家功能开发",
    "login-module-development": "登录模块开发",
}


@dataclass(frozen=True, slots=True)
class GardenFact:
    category: str
    kind: str
    text: str
    path: str
    line: int
    source: str = "source"
    confidence: str = "generated"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GardenserverRuleAdapter:
    """可解释地提取 gardenserver 的 Lua/Skynet/zn 约定。"""

    PATTERNS = (
        ("module_import", re.compile(r'\brequire\s*[ (]\s*["\']([^"\']+)["\']')),
        ("message_module", re.compile(r"zn\.func_mod\s*\(")),
        ("service_entry", re.compile(r"zn\.startup_(?:app|sf)\s*\(")),
        ("skynet_start", re.compile(r"skynet\.start\s*\(")),
        ("rpc_call", re.compile(r"(?:zapi\.cluster|zn\.(?:req|preq|post))")),
        ("avatar_registration", re.compile(r"(?:components|systems)\s*=\s*\{")),
        ("native_skynet_api", re.compile(r"skynet\.(?:call|send|getenv)\s*\(")),
        ("config_read", re.compile(r"(?:zn\.(?:getenv|sheetcfg|sheetdata)|require\s*[ (].*conf)")),
    )

    def __init__(self, root: str | Path, client: CodeGraphClient | None = None) -> None:
        self.root = Path(root).resolve()
        self.client = client

    def collect(self, category: str, *, sample_terms: Iterable[str] = ()) -> dict[str, Any]:
        if category not in CATEGORIES:
            raise ValueError(f"未知 gardenserver 指导类别：{category}")
        terms = [term.lower() for term in sample_terms]
        facts: list[GardenFact] = []
        paths = self._candidate_paths(category, terms)
        for path in paths:
            text = read_text(self.root / path)
            for line_number, line in enumerate(text.splitlines(), 1):
                for kind, pattern in self.PATTERNS:
                    if pattern.search(line):
                        confidence = "verified" if kind != "native_skynet_api" else "generated"
                        facts.append(GardenFact(category, kind, line.strip()[:240], path, line_number, "source", confidence))
        facts.extend(self._codegraph_facts(category, terms))
        deduped: dict[tuple[str, str, int, str], GardenFact] = {}
        for fact in facts:
            deduped[(fact.kind, fact.path, fact.line, fact.text)] = fact
        return {
            "category": category,
            "title": CATEGORIES[category],
            "samples": list(sample_terms),
            "facts": [fact.to_dict() for fact in sorted(deduped.values(), key=lambda item: (item.path, item.line, item.kind))],
            "source_hashes": {path: hash_file(self.root / path) for path in paths},
            "status": "fresh",
        }

    def _candidate_paths(self, category: str, terms: list[str]) -> list[str]:
        all_paths = sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".lua", ".proto", ".md", ".json", ".xml", ".yaml", ".yml", ".conf"}
            and ".project-kb" not in path.parts and ".git" not in path.parts
        )
        def has_any(path: str, needles: tuple[str, ...]) -> bool:
            lowered = path.lower()
            return any(needle in lowered for needle in needles)
        if category == "login-module-development":
            selected = [path for path in all_paths if has_any(path, ("login", "account", "session", "auth"))]
        elif category == "player-feature-development":
            selected = [path for path in all_paths if has_any(path, ("magent", "avatar", "game", "garden", "guild"))]
        else:
            selected = [path for path in all_paths if has_any(path, ("activity", "event", "activity", "reward", "timer", "task"))]
        for term in terms:
            selected.extend(path for path in all_paths if term in path.lower())
        return sorted(set(selected))

    def _codegraph_facts(self, category: str, terms: list[str]) -> list[GardenFact]:
        if self.client is None:
            return []
        facts: list[GardenFact] = []
        queries = {
            "login-module-development": ("login", "session", "auth"),
            "player-feature-development": ("garden", "avatar", "guild"),
            "activity-development": ("activity", "event", "reward"),
        }[category]
        for query in (*queries, *terms):
            try:
                results = self.client.query(query, limit=20)
            except CodeGraphError:
                continue
            for result in results:
                node = result.get("node", result)
                path = str(node.get("filePath", "")).replace("\\", "/")
                if not path:
                    continue
                facts.append(GardenFact(
                    category, "codegraph_symbol",
                    f"{node.get('kind', 'symbol')} {node.get('qualifiedName', node.get('name', query))}",
                    path, int(node.get("startLine", 1) or 1), "codegraph", "verified",
                ))
        return facts


class GuidanceEvidenceCollector:
    def __init__(self, root: str | Path, client: CodeGraphClient | None = None) -> None:
        self.adapter = GardenserverRuleAdapter(root, client)

    def collect_all(self) -> dict[str, dict[str, Any]]:
        return {
            "activity-development": self.adapter.collect("activity-development", sample_terms=("活动",)),
            "player-feature-development": self.adapter.collect("player-feature-development", sample_terms=("花园", "公会")),
            "login-module-development": self.adapter.collect("login-module-development", sample_terms=("登录",)),
        }
