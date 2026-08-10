from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .config import ProjectConfig
from .models import KnowledgeRecord, SourceReference
from .schemas import KNOWLEDGE_RECORD_SCHEMA, validate_instance
from .store import KnowledgeStore
from .util import atomic_json, atomic_write, hash_file, read_text, slug, utc_now


GENERATED_NOTICE = "<!-- 本文件由 project-kb 自动生成，请勿手动编辑。 -->"
TEMPLATE_MARKER = '<!-- project-kb:template status="unreviewed" -->'
SOURCE_MARKER = re.compile(r'<!--\s*project-kb:source\s+(file|symbol)="([^"]+)"\s*-->')
LANGUAGE_TITLES = {"Configuration": "配置", "Python": "Python"}
SYMBOL_KIND_TITLES = {"module": "模块", "class": "类", "function": "函数", "method": "方法"}
RELATION_KIND_TITLES = {"calls": "调用", "imports": "导入", "inherits": "继承"}


class KnowledgeGenerator:
    def __init__(self, root: Path, config: ProjectConfig, store: KnowledgeStore):
        self.root = root
        self.config = config
        self.store = store
        self.generated_root = root / config.generated_root
        self.drafts_root = root / config.drafts_root
        self.curated_root = root / config.curated_root
        self.decisions_root = root / config.decisions_root
        self.now = utc_now()
        self.commit = store.get_meta("head_commit")

    def generate(self, refresh_generated: bool = True) -> list[KnowledgeRecord]:
        self._ensure_curated_templates()
        if refresh_generated:
            generated = [self._project_map(), *self._module_maps(), self._routes(), self._entrypoints(), self._test_map()]
            generated = [record for record in generated if record is not None]
            for record in generated:
                self.store.upsert_knowledge(record)
            self.store.delete_missing_knowledge({record.id for record in generated}, "generated")

        curated = self._curated_records()
        for record in curated:
            self.store.upsert_knowledge(record)
        self.store.delete_missing_knowledge({record.id for record in curated}, "curated")

        decisions = self._decision_records()
        for record in decisions:
            self.store.upsert_knowledge(record)
        self.store.delete_missing_knowledge({record.id for record in decisions}, "decision")

        drafts = self._draft_records()
        for record in drafts:
            self.store.upsert_knowledge(record)
        self.store.delete_missing_knowledge({record.id for record in drafts}, "draft")

        records = self.store.all_knowledge()
        self._knowledge_index(records)
        self._manifest(records)
        return records

    def _source_hashes(self, sources: Iterable[SourceReference]) -> dict[str, str]:
        values: dict[str, str] = {}
        for source in sources:
            key = source.id or source.path
            if not key:
                continue
            if source.type == "symbol":
                row = self.store.connection.execute("SELECT hash FROM symbols WHERE id = ?", (source.id,)).fetchone()
                if row:
                    values[key] = row["hash"]
            elif source.path:
                row = self.store.connection.execute("SELECT hash FROM files WHERE path = ?", (source.path,)).fetchone()
                if row:
                    values[key] = row["hash"]
                else:
                    path = self.root / source.path
                    if path.is_file():
                        values[key] = hash_file(path)
        return values

    def _record(self, *, record_id: str, kind: str, title: str, relative_path: str, content: str,
                sources: list[SourceReference], tags: list[str]) -> KnowledgeRecord:
        atomic_write(self.root / relative_path, content)
        return KnowledgeRecord(
            id=record_id,
            kind=kind,
            title=title,
            path=relative_path,
            ownership="generated",
            confidence="generated",
            status="fresh",
            sources=sources,
            source_commit=self.commit,
            source_hashes=self._source_hashes(sources),
            last_generated_at=self.now,
            tags=tags,
            content=content,
        )

    def _project_map(self) -> KnowledgeRecord:
        files = self.store.rows("SELECT path, language, module, parser, parse_error FROM files ORDER BY path")
        languages: dict[str, int] = defaultdict(int)
        modules: dict[str, int] = defaultdict(int)
        for item in files:
            languages[item["language"]] += 1
            modules[item["module"]] += 1
        counts = self.store.counts()
        language_rows = "\n".join(
            f"| {LANGUAGE_TITLES.get(name, name)} | {count} |"
            for name, count in sorted(languages.items(), key=lambda pair: (-pair[1], pair[0]))
        ) or "| 无 | 0 |"
        module_rows = "\n".join(f"| [{name}](modules/{slug(name)}.md) | {count} |" for name, count in sorted(modules.items())) or "| 无 | 0 |"
        errors = [item for item in files if item["parse_error"]]
        error_text = "\n".join(f"- `{item['path']}`：{item['parse_error']}" for item in errors) or "- 无"
        content = f"""{GENERATED_NOTICE}

# 项目地图：{self.config.project_name}

生成时间：`{self.now}`；来源提交：`{self.commit or '未提交'}`。

## 概览

| 指标 | 数量 |
| --- | ---: |
| 文件 | {counts['files']} |
| 符号 | {counts['symbols']} |
| 关系 | {counts['relations']} |
| 模块 | {counts['modules']} |
| 路由 | {counts['routes']} |
| 未解析关系 | {counts['unresolved_relations']} |

## 语言分布

| 语言 | 文件数 |
| --- | ---: |
{language_rows}

## 模块

| 模块 | 文件数 |
| --- | ---: |
{module_rows}

## 静态分析边界

- Python 符号和语法通过标准 AST 提取。
- 其他语言使用保守的模式提取，因此可信度较低。
- 动态分派、反射、运行时依赖注入以及运行时生成的路由可能无法被识别。

## 解析错误

{error_text}
"""
        sources = [SourceReference(type="file", path=item["path"]) for item in files]
        return self._record(
            record_id="generated.project-map", kind="project", title=f"项目地图：{self.config.project_name}",
            relative_path=f"{self.config.generated_root}/project-map.md", content=content,
            sources=sources, tags=["project", "architecture", *languages.keys()],
        )

    def _module_maps(self) -> list[KnowledgeRecord]:
        records: list[KnowledgeRecord] = []
        modules = self.store.rows("SELECT DISTINCT module FROM files ORDER BY module")
        for module_row in modules:
            module = module_row["module"]
            files = self.store.rows("SELECT path, language FROM files WHERE module = ? ORDER BY path", [module])
            symbols = self.store.rows(
                "SELECT id, name, kind, path, line, confidence FROM symbols WHERE path IN (SELECT path FROM files WHERE module = ?) AND kind != 'module' ORDER BY path, line LIMIT 300",
                [module],
            )
            relations = self.store.rows(
                "SELECT source, target, kind, confidence, resolved FROM relations WHERE path IN (SELECT path FROM files WHERE module = ?) ORDER BY confidence DESC LIMIT 150",
                [module],
            )
            symbol_total = self.store.connection.execute(
                "SELECT COUNT(*) FROM symbols WHERE path IN (SELECT path FROM files WHERE module = ?) AND kind != 'module'",
                (module,),
            ).fetchone()[0]
            relation_total = self.store.connection.execute(
                "SELECT COUNT(*) FROM relations WHERE path IN (SELECT path FROM files WHERE module = ?)",
                (module,),
            ).fetchone()[0]
            file_lines = "\n".join(
                f"- `{item['path']}`（{LANGUAGE_TITLES.get(item['language'], item['language'])}）"
                for item in files
            ) or "- 无"
            symbol_lines = "\n".join(
                f"- `{item['id']}`：{SYMBOL_KIND_TITLES.get(item['kind'], item['kind'])}，"
                f"位于 `{item['path']}:{item['line']}`（可信度 {item['confidence']:.2f}）"
                for item in symbols
            ) or "- 未检测到符号"
            relation_lines = "\n".join(
                f"- `{item['source']}` {RELATION_KIND_TITLES.get(item['kind'], item['kind'])} `{item['target']}`"
                f"（可信度 {item['confidence']:.2f}，{'已解析' if item['resolved'] else '未解析'}）"
                for item in relations
            ) or "- 未检测到结构关系"
            symbol_notice = (
                f"> 符号内容已截断：共 {symbol_total} 个，当前页面仅展示前 {len(symbols)} 个；"
                "请使用 knowledge_context 或 knowledge_search 获取精确范围。\n\n"
                if symbol_total > len(symbols) else ""
            )
            relation_notice = (
                f"> 关系内容已截断：共 {relation_total} 条，当前页面仅展示前 {len(relations)} 条；"
                "请使用 knowledge_impact 获取完整的任务相关子图。\n\n"
                if relation_total > len(relations) else ""
            )
            content = f"""{GENERATED_NOTICE}

# 模块：{module}

## 文件

{file_lines}

## 符号

{symbol_notice}{symbol_lines}

## 结构关系

{relation_notice}{relation_lines}
"""
            sources = [SourceReference(type="file", path=item["path"]) for item in files]
            relative = f"{self.config.generated_root}/modules/{slug(module)}.md"
            records.append(self._record(
                record_id=f"generated.module.{slug(module)}", kind="module", title=f"模块：{module}",
                relative_path=relative, content=content, sources=sources, tags=["module", module],
            ))
        return records

    def _entrypoints(self) -> KnowledgeRecord:
        relations = self.store.rows(
            "SELECT source, target, kind, path, line, confidence FROM relations "
            "WHERE kind IN ('service_start', 'dispatch') ORDER BY path, line, kind"
        )
        rows: list[str] = []
        sources: list[SourceReference] = []
        for item in relations:
            label = "Skynet 启动" if item["kind"] == "service_start" else "协议派发"
            rows.append(
                f"| {label} | {item['source']} | {item['target']} | "
                f"{item['path']}:{item['line']} | {item['confidence']:.2f} |"
            )
            sources.append(SourceReference(type="file", path=item["path"], line=item["line"]))
        filename_rows = self.store.rows(
            "SELECT path FROM files WHERE language = 'Lua' "
            "AND (lower(path) LIKE '%/main.lua' OR lower(path) LIKE 'main.lua' "
            "OR lower(path) LIKE '%/bootstrap.lua' OR lower(path) LIKE 'bootstrap.lua' "
            "OR lower(path) LIKE '%/start.lua' OR lower(path) LIKE 'start.lua') ORDER BY path"
        )
        known_paths = {item["path"] for item in relations}
        for item in filename_rows:
            if item["path"] in known_paths:
                continue
            rows.append(
                f"| 文件名推断入口 | {item['path']}::<module> | 需要人工确认 | "
                f"{item['path']}:1 | 0.55 |"
            )
            sources.append(SourceReference(type="file", path=item["path"], line=1))
        if not sources:
            sources.extend([SourceReference(type="file", path="src/project_knowledge/engine.py"), SourceReference(type="file", path="src/project_knowledge/knowledge.py")])
        body = "\n".join(rows) or "| - | 未检测到静态入口 | - | - | - |"
        content = f"""{GENERATED_NOTICE}

# Lua/Skynet 入口证据

本页只记录源码中可定位的启动和协议派发证据。动态启动命令、运行时生成的协议名和服务发现结果必须现场验证。

| 类型 | 来源符号 | 目标 | 源码位置 | 置信度 |
| --- | --- | --- | --- | ---: |
{body}

## 使用约束

- “Skynet 启动”表示检测到 skynet.start 或 skynetx.start。
- “协议派发”表示检测到 skynet.dispatch、protocol.dispatch、protocol.run 或 protocol.exec。
- “文件名推断入口”只表示文件命名线索，不是已验证的启动入口。
"""
        return self._record(
            record_id="generated.entrypoints", kind="entrypoint", title="Lua/Skynet 入口证据",
            relative_path=f"{self.config.generated_root}/entrypoints.md", content=content,
            sources=sources, tags=["entrypoints", "lua", "skynet", "runtime-boundary"],
        )

    def _routes(self) -> KnowledgeRecord:
        routes = self.store.rows("SELECT method, route, handler, path, line FROM routes ORDER BY route, method")
        rows = "\n".join(
            f"| {item['method']} | `{item['route']}` | `{item['handler']}` | `{item['path']}:{item['line']}` |"
            for item in routes
        ) or "| - | 未检测到静态路由 | - | - |"
        content = f"""{GENERATED_NOTICE}

# 路由

此处仅列出静态路由声明，运行时注册的路由可能不会显示。

| 方法 | 路由 | 处理器 | 来源 |
| --- | --- | --- | --- |
{rows}
"""
        sources = [SourceReference(type="file", path=item["path"], line=item["line"]) for item in routes]
        return self._record(
            record_id="generated.routes", kind="route", title="路由",
            relative_path=f"{self.config.generated_root}/routes.md", content=content,
            sources=sources, tags=["routes", "entrypoints"],
        )

    def _test_map(self) -> KnowledgeRecord:
        files = self.store.rows(
            "SELECT path, module FROM files WHERE path LIKE '%test%' OR path LIKE '%spec%' ORDER BY path"
        )
        rows: list[str] = []
        for item in files:
            symbols = self.store.connection.execute(
                "SELECT COUNT(*) FROM symbols WHERE path = ? AND (name LIKE 'test%' OR name LIKE '%Test%')", (item["path"],)
            ).fetchone()[0]
            rows.append(f"| `{item['path']}` | {item['module']} | {symbols} |")
        body = "\n".join(rows) or "| 未检测到测试 | - | 0 |"
        content = f"""{GENERATED_NOTICE}

# 测试地图

| 测试文件 | 模块 | 测试类符号数 |
| --- | --- | ---: |
{body}
"""
        sources = [SourceReference(type="file", path=item["path"]) for item in files]
        return self._record(
            record_id="generated.test-map", kind="test", title="测试地图",
            relative_path=f"{self.config.generated_root}/test-map.md", content=content,
            sources=sources, tags=["tests", "verification"],
        )

    def _ensure_curated_templates(self) -> None:
        templates = {
            self.curated_root / "architecture.md": f"""{TEMPLATE_MARKER}

# 架构

在此记录模块职责、边界和架构原则。

完成核验后请删除顶部的 `project-kb:template` 标记；删除前本页仅按推断信息处理。

当陈述依赖代码事实时，请添加来源标记：

`&lt;!-- project-kb:source file="src/example.py" --&gt;`
""",
            self.curated_root / "conventions.md": f"""{TEMPLATE_MARKER}

# 约定

在此记录项目特有的编码、测试和评审约定。

完成核验后请删除顶部的 `project-kb:template` 标记。
""",
            self.curated_root / "glossary.md": f"""{TEMPLATE_MARKER}

# 术语表

在此记录领域术语及其在项目中的准确含义。

完成核验后请删除顶部的 `project-kb:template` 标记。
""",
        }
        for path, content in templates.items():
            if not path.exists():
                atomic_write(path, content)
        self.decisions_root.mkdir(parents=True, exist_ok=True)
        for child in ["modules", "workflows", "recipes"]:
            (self.curated_root / child).mkdir(parents=True, exist_ok=True)

    def _document_records(self, base: Path, ownership: str) -> list[KnowledgeRecord]:
        records: list[KnowledgeRecord] = []
        if not base.exists():
            return records
        for path in sorted(base.rglob("*.md")):
            relative = path.relative_to(self.root).as_posix()
            content = read_text(path)
            title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else path.stem.replace("-", " ").title()
            if ownership == "decision":
                kind = "decision"
            elif ownership == "draft" and path.parent.name == "features":
                kind = "feature-guide"
            else:
                kind = path.parent.name if path.parent != base else path.stem
            sources = [
                SourceReference(type=source_type, path=value if source_type == "file" else None, id=value if source_type == "symbol" else None)
                for source_type, value in SOURCE_MARKER.findall(content)
            ]
            if ownership == "draft" and path.parent.name == "features":
                record_id = f"draft.feature.{path.stem}"
            else:
                record_id = f"{ownership}.{slug(path.relative_to(base).with_suffix('').as_posix()).replace('-', '.')}"
            existing = self.store.get_knowledge(record_id)
            current_hashes = self._source_hashes(sources)
            is_template = TEMPLATE_MARKER in content
            expected_keys = {source.id or source.path for source in sources}
            missing = any(key not in current_hashes for key in expected_keys if key)
            content_changed = bool(existing and existing.content != content)
            if missing:
                status = "stale"
                baseline = existing.source_hashes if existing else current_hashes
            elif content_changed:
                status = "fresh"
                baseline = current_hashes
            elif existing and existing.source_hashes and existing.source_hashes != current_hashes:
                status = "potentially_stale"
                baseline = existing.source_hashes
            else:
                status = "fresh"
                baseline = current_hashes
            records.append(KnowledgeRecord(
                id=record_id, kind=kind, title=title, path=relative, ownership=ownership,
                confidence="inferred" if is_template else ("verified" if ownership in {"curated", "decision"} else "generated"),
                status=status, sources=sources,
                source_commit=self.commit if content_changed or not existing else existing.source_commit,
                source_hashes=baseline,
                last_verified_at=None if is_template else (self.now if content_changed or not existing else existing.last_verified_at),
                tags=[ownership, *(["template"] if is_template else []), *path.relative_to(base).parts[:-1]], content=content,
            ))
        return records

    def _curated_records(self) -> list[KnowledgeRecord]:
        return self._document_records(self.curated_root, "curated")

    def _decision_records(self) -> list[KnowledgeRecord]:
        return self._document_records(self.decisions_root, "decision")

    def _draft_records(self) -> list[KnowledgeRecord]:
        return self._document_records(self.drafts_root, "draft")

    def _knowledge_index(self, records: list[KnowledgeRecord]) -> None:
        groups: dict[str, list[KnowledgeRecord]] = defaultdict(list)
        for record in records:
            groups[record.ownership].append(record)
        ownership_titles = {"generated": "自动生成", "draft": "语义草案", "curated": "人工维护", "decision": "架构决策"}
        status_titles = {
            "fresh": "新鲜",
            "potentially_stale": "可能过期",
            "stale": "已过期",
            "conflicted": "有冲突",
        }
        confidence_titles = {"verified": "已验证", "generated": "自动生成", "inferred": "推断"}
        sections: list[str] = [GENERATED_NOTICE, "", f"# 项目知识库：{self.config.project_name}", ""]
        for ownership in ["draft", "curated", "decision", "generated"]:
            sections.extend([f"## {ownership_titles[ownership]}", ""])
            for record in sorted(groups.get(ownership, []), key=lambda item: item.id):
                status = status_titles.get(record.status, record.status)
                confidence = confidence_titles.get(record.confidence, record.confidence)
                sections.append(
                    f"- [{record.title}]({Path(record.path).relative_to(self.config.knowledge_root).as_posix()})"
                    f" - 状态：`{status}`；可信度：`{confidence}`"
                )
            if not groups.get(ownership):
                sections.append("- 无")
            sections.append("")
        atomic_write(self.root / self.config.knowledge_root / "index.md", "\n".join(sections))

    def _manifest(self, records: list[KnowledgeRecord]) -> None:
        serialized_records = [record.to_dict() for record in records]
        for index, record in enumerate(serialized_records):
            validate_instance(record, KNOWLEDGE_RECORD_SCHEMA, f"$.records[{index}]")
        manifest = {
            "schema_version": 1,
            "project": self.config.project_name,
            "generated_at": self.now,
            "source_commit": self.commit,
            "records": serialized_records,
        }
        atomic_json(self.root / ".project-kb" / "manifest.json", manifest)
