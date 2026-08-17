# Remove Builtin Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the builtin code index engine and make the real CodeGraph public CLI adapter the only production source of code facts.

**Architecture:** `CodeIndexEngine` becomes a snapshot-and-query protocol and `CodeGraphEngine` is its only production implementation. `ProjectService` stores CodeGraph file snapshots plus knowledge metadata, clears all legacy local code facts transactionally, and `KnowledgeAPI` obtains symbols and relations live from CodeGraph without SQLite, grep, or parser fallback.

**Tech Stack:** Python 3.11+, dataclasses/protocols, SQLite, argparse CLI, CodeGraph 1.5 public CLI, pytest/unittest, JSON Schema, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-18-remove-builtin-engine-design.md`

## Global Constraints

- Target release is `0.1.30`; work package is WP-13; requirements are BE-001 through BE-009.
- Production supports only `engine: codegraph`; `engine: builtin` must return `unsupported_engine` and must not be rewritten automatically.
- Do not read CodeGraph private databases or undocumented files.
- Do not use local AST, regex parsing, grep, or historical SQLite symbols/relations as a runtime fallback.
- In 0.1.30, keep the legacy `symbols`, `relations`, and `routes` tables empty for schema compatibility; runtime code must not read them.
- Remove unsupported route and entrypoint generation instead of producing empty placeholder knowledge.
- Do not lower WP-12A thresholds or turn a failing evaluation report into a release baseline.
- Run repository commands through `rtk` and execute Project Knowledge from this worktree's `src` directory.
- Run `python scripts/bump_version.py "移除 builtin engine，CodeGraph 成为唯一代码事实源"` exactly once for this implementation batch.
- Generated knowledge synchronization does not trigger another version bump.

## File Structure

- Create `src/project_knowledge/errors.py`: structured Project Knowledge error base and CodeGraph/config error codes.
- Modify `src/project_knowledge/config.py`: CodeGraph-only defaults and strict engine validation.
- Modify `src/project_knowledge/schemas.py`: CodeGraph-only config schema.
- Modify `src/project_knowledge/cli.py`: structured CLI error rendering.
- Modify `src/project_knowledge/engine.py`: retain only engine protocol, file snapshot types, and factory.
- Modify `src/project_knowledge/codegraph.py`: implement the snapshot/query protocol without builtin delegation.
- Modify `src/project_knowledge/store.py`: atomically replace file snapshots and clear legacy code facts.
- Modify `src/project_knowledge/service.py`: initialize, sync, rebuild, status, and watch from CodeGraph snapshots.
- Modify `src/project_knowledge/retrieval.py`: remove local symbol/relation queries and builtin provenance.
- Modify `src/project_knowledge/knowledge.py`: generate only knowledge supported by CodeGraph public output or curated evidence.
- Modify `src/project_knowledge/real_project.py`: use a CodeGraph-initialized temporary mirror; remove builtin entrypoint inspection.
- Create `tests/codegraph_fakes.py`: explicit, non-parsing fake engine for unit and integration tests.
- Create `tests/test_service_codegraph_snapshot.py`: snapshot migration and rollback coverage.
- Modify existing engine, config, service, retrieval, evaluation, delivery, and knowledge tests to declare CodeGraph facts explicitly.
- Modify `.github/workflows/quality.yml`: require real Adapter validation and remove references to nonexistent baselines.
- Modify `.project-kb.yml`, `README.md`, audit/plan/ADR/knowledge docs, `CHANGELOG.md`, and the unique version source through the bump script.

---

### Task 1: CodeGraph-Only Configuration And Structured Errors

**Requirements:** BE-002, BE-003, BE-004

**Files:**
- Create: `src/project_knowledge/errors.py`
- Modify: `src/project_knowledge/config.py`
- Modify: `src/project_knowledge/schemas.py`
- Modify: `src/project_knowledge/cli.py`
- Test: `tests/test_config.py`
- Test: `tests/test_schemas.py`
- Test: `tests/test_integration.py`

**Interfaces:**
- Produces: `ProjectKnowledgeError(code: str, message: str, details: dict[str, object])`
- Produces: `UnsupportedEngineError(configured_engine: str)` with `code == "unsupported_engine"`
- Produces: `ProjectKnowledgeError.to_dict() -> dict[str, object]`
- Produces: `SUPPORTED_ENGINES: tuple[str, ...] = ("codegraph",)`
- Consumes: existing `ProjectConfig.load()`, `ProjectConfig.dump()`, and CLI `main(argv)`.

- [ ] **Step 1: Add failing default, legacy-config, schema, and CLI tests**

```python
from project_knowledge.config import ProjectConfig
from project_knowledge.errors import UnsupportedEngineError

def test_default_engine_is_codegraph():
    assert ProjectConfig().engine == "codegraph"

def test_legacy_builtin_config_is_rejected(tmp_path):
    (tmp_path / ".project-kb.yml").write_text(
        "version: 1\nindex:\n  engine: builtin\n", encoding="utf-8"
    )
    with pytest.raises(UnsupportedEngineError) as raised:
        ProjectConfig.load(tmp_path)
    assert raised.value.to_dict() == {
        "error": "unsupported_engine",
        "configured_engine": "builtin",
        "supported_engines": ["codegraph"],
        "migration": "set index.engine to codegraph and initialize CodeGraph for this project",
    }
```

Add a schema assertion that `CONFIG_SCHEMA["properties"]["index"]["properties"]["engine"]["enum"] == ["codegraph"]`. Add a CLI test that invokes `main(["status", str(root), "--json"])`, expects exit code `2`, and parses the exact JSON error above from stdout.

- [ ] **Step 2: Run the focused tests and capture RED**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_schemas.py tests/test_integration.py -q`

Expected: failures show the default is `builtin`, the schema accepts builtin, and the CLI emits an unstructured stderr string.

- [ ] **Step 3: Implement the structured error types**

```python
class ProjectKnowledgeError(RuntimeError):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def to_dict(self) -> dict[str, object]:
        return {"error": self.code, **self.details}


class UnsupportedEngineError(ProjectKnowledgeError):
    def __init__(self, configured_engine: str) -> None:
        super().__init__(
            "unsupported_engine",
            f"unsupported index engine: {configured_engine}",
            configured_engine=configured_engine,
            supported_engines=["codegraph"],
            migration="set index.engine to codegraph and initialize CodeGraph for this project",
        )
```

- [ ] **Step 4: Enforce the CodeGraph-only configuration**

Set `ProjectConfig.engine` and the load fallback to `"codegraph"`. Add `ProjectConfig.__post_init__()` that raises `UnsupportedEngineError` unless `self.engine in SUPPORTED_ENGINES`. Change the config schema enum to `['codegraph']`.

Update CLI exception handling so `ProjectKnowledgeError` returns exit code `2`, prints `error.to_dict()` to stdout for `--json`, and prints `project-kb: <message>` to stderr otherwise. Leave unexpected `OSError`, `ValueError`, `KeyError`, and JSON errors on exit code `1`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_schemas.py tests/test_integration.py -q`

Expected: all selected tests pass; legacy builtin configuration is rejected before engine creation.

- [ ] **Step 6: Commit the configuration contract**

```powershell
rtk git add src/project_knowledge/errors.py src/project_knowledge/config.py src/project_knowledge/schemas.py src/project_knowledge/cli.py tests/test_config.py tests/test_schemas.py tests/test_integration.py
rtk git commit -m "feat: require CodeGraph engine configuration"
```

### Task 2: Snapshot-Only Engine Protocol And CodeGraph Adapter

**Requirements:** BE-001, BE-002, BE-003, BE-005, BE-006

**Files:**
- Modify: `src/project_knowledge/engine.py`
- Modify: `src/project_knowledge/codegraph.py`
- Modify: `src/project_knowledge/errors.py`
- Create: `tests/codegraph_fakes.py`
- Modify: `tests/test_codegraph.py`
- Modify: `tests/test_engine.py`
- Modify: `scripts/validate_codegraph_adapter.py`

**Interfaces:**
- Produces: `CodeIndexSnapshot(snapshot_id: str, files: tuple[IndexedFile, ...])`
- Produces: `CodeIndexEngine.snapshot(root: Path, config: ProjectConfig) -> CodeIndexSnapshot`
- Produces: `create_engine(config: ProjectConfig) -> CodeGraphEngine`
- Produces: `CodeGraphError(ProjectKnowledgeError)` using reason codes `cli_missing`, `project_not_initialized`, `command_failed`, and `invalid_adapter_output`.
- Produces: `FakeCodeGraphEngine` whose constructor receives explicit snapshots, symbols, source, trace, impact, and affected-test mappings; it never parses source text.

- [ ] **Step 1: Write failing protocol and no-fallback tests**

```python
def test_codegraph_snapshot_normalizes_public_file_output(tmp_path):
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("def run(): return 1\n", encoding="utf-8")
    engine = CodeGraphEngine(ProjectConfig(codegraph_command="fake"))
    engine.client = fake_client(files=[{
        "path": "src/app.py", "language": "python", "contentHash": "sha256:abc"
    }])
    snapshot = engine.snapshot(tmp_path, engine.config)
    assert snapshot.snapshot_id
    assert snapshot.files[0].path == "src/app.py"
    assert snapshot.files[0].content_hash == "sha256:abc"


def test_engine_has_no_parse_or_entrypoint_contract():
    assert "parse" not in CodeIndexEngine.__dict__
    assert "entrypoints" not in CodeIndexEngine.__dict__
```

Add failures for an absolute/out-of-project path, malformed JSON, CLI missing, project not initialized, and `CodeGraphEngine.__dict__` containing neither `_builtin_engine` nor `_builtin`.

- [ ] **Step 2: Run focused tests and capture RED**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_codegraph.py tests/test_engine.py -q`

Expected: `CodeIndexSnapshot` and `snapshot()` are absent, and the fallback attributes still exist.

- [ ] **Step 3: Define the snapshot protocol**

Keep `IndexedFile` in `engine.py` and add:

```python
@dataclass(frozen=True, slots=True)
class CodeIndexSnapshot:
    snapshot_id: str
    files: tuple[IndexedFile, ...]


class CodeIndexEngine(Protocol):
    def snapshot(self, root: Path, config: ProjectConfig) -> CodeIndexSnapshot: ...
    def initialize(self, root: Path, config: ProjectConfig) -> dict[str, object]: ...
    def sync(self, root: Path, config: ProjectConfig, previous: dict[str, str] | None = None) -> dict[str, object]: ...
    def diagnose(self, root: Path) -> dict[str, object]: ...
    def status(self) -> dict[str, object]: ...
    def search_symbols(self, root: Path, config: ProjectConfig, query: str, limit: int = 20) -> list[Symbol]: ...
    def get_source(self, root: Path, path: str, start_line: int | None = None, end_line: int | None = None) -> str: ...
    def trace(self, root: Path, symbol_id: str, config: ProjectConfig, max_depth: int = 1, limit: int = 200) -> list[Relation]: ...
    def impact(self, root: Path, config: ProjectConfig, files: list[str] | None = None, symbols: list[str] | None = None, max_hops: int = 1, max_relations: int = 500) -> dict[str, object]: ...
    def affected_tests(self, root: Path, config: ProjectConfig, files: list[str]) -> list[str]: ...
```

Use real imports for `Symbol` and `Relation`; do not leave `Any` where a current domain type exists.

- [ ] **Step 4: Implement CodeGraph snapshot and structured diagnostics**

Define the Adapter exception contract exactly once:

```python
class CodeGraphError(ProjectKnowledgeError):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(code, message, **details)
```

`CodeGraphEngine.snapshot()` must call `CodeGraphClient.snapshot()`, validate all paths remain within `root`, stat each existing source file for size/mtime, map public language names, and return an immutable, path-sorted `CodeIndexSnapshot`. It must not read file contents except when `CodeGraphClient.snapshot()` needs a content hash missing from public output.

Delete `_builtin`, `_builtin_engine()`, `parse()`, and `entrypoints()` from `CodeGraphEngine`. Update capabilities to only `initialize`, `sync`, `snapshot`, `symbols`, `search_symbols`, `get_source`, `trace`, `impact`, `affected_tests`, and `calls`. Remove the compatibility-cache limitation text.

- [ ] **Step 5: Add a reusable explicit fake engine**

```python
@dataclass
class FakeCodeGraphEngine:
    snapshot_value: CodeIndexSnapshot
    symbols_by_query: dict[str, list[Symbol]] = field(default_factory=dict)
    sources_by_path: dict[str, str] = field(default_factory=dict)
    traces_by_symbol: dict[str, list[Relation]] = field(default_factory=dict)
    impact_value: dict[str, object] = field(default_factory=lambda: {
        "affected_files": [], "affected_symbols": [], "affected_modules": [],
        "affected_tests": [], "relations": [], "limitations": []
    })
    affected_by_file: dict[str, list[str]] = field(default_factory=dict)
    available: bool = True

    def snapshot(self, root, config):
        if not self.available:
            raise CodeGraphError("command_failed", "fake CodeGraph is unavailable")
        return self.snapshot_value
```

Implement every protocol method with explicit mappings. No method may inspect or parse source contents.

- [ ] **Step 6: Extend the real Adapter validator**

Add `engine.snapshot()` to `scripts/validate_codegraph_adapter.py`; assert the fixture files are returned and all paths are relative. Keep the existing init, files, query, trace, impact, and affected checks.

- [ ] **Step 7: Run focused and real Adapter tests**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_codegraph.py tests/test_engine.py tests/test_codegraph_validation.py -q`

Run: `rtk .\.venv\Scripts\python.exe scripts/validate_codegraph_adapter.py --json`

Expected: focused tests pass and the real report contains `"passed": true` plus `snapshot` in `checks`.

- [ ] **Step 8: Commit the engine boundary**

```powershell
rtk git add src/project_knowledge/engine.py src/project_knowledge/codegraph.py src/project_knowledge/errors.py tests/codegraph_fakes.py tests/test_codegraph.py tests/test_engine.py scripts/validate_codegraph_adapter.py
rtk git commit -m "refactor: define CodeGraph snapshot engine contract"
```

### Task 3: Transactional Snapshot Storage And Service Lifecycle

**Requirements:** BE-003, BE-005, BE-009

**Files:**
- Modify: `src/project_knowledge/store.py`
- Modify: `src/project_knowledge/service.py`
- Modify: `src/project_knowledge/retrieval.py` (constructor injection only)
- Create: `tests/test_service_codegraph_snapshot.py`
- Modify: `tests/test_integration.py`
- Modify: `tests/test_watch_wp07.py`
- Modify: `tests/test_finalization.py`

**Interfaces:**
- Consumes: `CodeIndexSnapshot` and `CodeIndexEngine` from Task 2.
- Produces: `KnowledgeStore.replace_code_snapshot(snapshot: CodeIndexSnapshot) -> None`
- Produces: `ProjectService(path, *, engine_factory=create_engine)`; the factory is retained and reused after config reload.
- Produces: `KnowledgeAPI(project=".", *, service: ProjectService | None = None)` for explicit test injection.

- [ ] **Step 1: Write failing migration, rollback, and no-parse tests**

Build a legacy database containing one file, symbol, relation, and route. Then run service sync with a fake CodeGraph snapshot containing `src/new.py` and assert:

```python
with KnowledgeStore(service.db_path, readonly=True) as store:
    assert store.file_hashes() == {"src/new.py": "sha256:new"}
    assert store.rows("SELECT * FROM symbols") == []
    assert store.rows("SELECT * FROM relations") == []
    assert store.rows("SELECT * FROM routes") == []
```

Add a rollback test where the fake engine raises before snapshot completion and assert the legacy database remains byte-for-byte unchanged. Add a spy engine that raises `AssertionError` if `parse` or `discover` is accessed.

- [ ] **Step 2: Run focused tests and capture RED**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_service_codegraph_snapshot.py tests/test_integration.py tests/test_watch_wp07.py tests/test_finalization.py -q`

Expected: service still calls `discover()` and `_parse_stable()` and legacy code facts remain.

- [ ] **Step 3: Add atomic snapshot replacement to the store**

```python
def replace_code_snapshot(self, snapshot: CodeIndexSnapshot) -> None:
    self.connection.execute("DELETE FROM routes")
    self.connection.execute("DELETE FROM relations")
    self.connection.execute("DELETE FROM symbols")
    self.connection.execute("DELETE FROM files")
    self.connection.executemany(
        "INSERT INTO files(path, language, module, size, mtime_ns, hash, parser, parse_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (item.path, item.language, item.module, item.size, item.mtime_ns,
             item.content_hash, "codegraph-snapshot", None)
            for item in snapshot.files
        ],
    )
    self.set_meta("codegraph_snapshot_id", snapshot.snapshot_id)
```

Call this method only inside an existing `KnowledgeStore.transaction()` so snapshot and metadata updates commit or roll back together.

- [ ] **Step 4: Refactor service lifecycle around snapshots**

Replace every service call to `discover()` with `snapshot()`. Remove `_parse_stable()`. Change `_atomic_rebuild()` to accept `CodeIndexSnapshot`, preserve reviewed knowledge/guidance, call `replace_code_snapshot()`, and generate supported knowledge.

For sync, compare `snapshot.files` hashes with `store.file_hashes()`, call `replace_code_snapshot()` once when the snapshot or Git/knowledge state changes, and retain the existing changed/deleted file report. For dry-run, call `diagnose()` and `snapshot()` but do not initialize CodeGraph or write files.

For status, Adapter failure must return `content_fresh: false`, `pending_files: []`, `freshness_unknown: true`, and the unavailable engine diagnostic; it must not report historical files as current. For commands that require a current snapshot, re-raise the structured CodeGraph error.

- [ ] **Step 5: Add explicit engine injection**

Store the supplied factory as `self._engine_factory`. Construct the engine with it in both `__init__` and `initialize()` after config reload. Let `KnowledgeAPI` accept an already constructed `ProjectService` so tests use declared fake facts without monkeypatching global production functions.

- [ ] **Step 6: Update lifecycle tests to use declared snapshots**

Replace builtin-derived symbol count assertions with file snapshot, Adapter call, knowledge preservation, and empty legacy table assertions. Watch tests should mutate the fake snapshot between heartbeats instead of relying on local parser discovery.

- [ ] **Step 7: Run lifecycle tests and verify GREEN**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_service_codegraph_snapshot.py tests/test_integration.py tests/test_watch_wp07.py tests/test_finalization.py -q`

Expected: all selected tests pass; no fake defines `parse()` or `discover()`.

- [ ] **Step 8: Commit the snapshot lifecycle**

```powershell
rtk git add src/project_knowledge/store.py src/project_knowledge/service.py src/project_knowledge/retrieval.py tests/test_service_codegraph_snapshot.py tests/test_integration.py tests/test_watch_wp07.py tests/test_finalization.py
rtk git commit -m "refactor: persist CodeGraph file snapshots"
```

### Task 4: Delete Builtin Parsers And Unsupported Generated Facts

**Requirements:** BE-001, BE-005, BE-006, BE-008

**Files:**
- Modify: `src/project_knowledge/engine.py`
- Modify: `src/project_knowledge/models.py`
- Modify: `src/project_knowledge/store.py`
- Modify: `src/project_knowledge/knowledge.py`
- Modify: `src/project_knowledge/real_project.py`
- Delete: `tests/test_engine_wp01_wp02.py`
- Modify: `tests/test_wp02_evidence.py`
- Modify: `tests/test_wp02_knowledge.py`
- Modify: `tests/test_engine.py`
- Modify: `tests/test_evaluate.py`

**Interfaces:**
- Consumes: snapshot-only service from Task 3.
- Produces: `run_readonly_mirror(source, max_files=None)` backed by a temporary CodeGraph initialization.
- Removes: `BuiltinCodeIndexEngine`, `PythonParser`, `LuaParser`, `GenericParser`, `ParseResult`, engine `entrypoints()`, generated route and entrypoint records.

- [ ] **Step 1: Write failing absence and generated-output tests**

```python
def test_builtin_parser_symbols_are_not_exported():
    import project_knowledge.engine as engine
    for name in ("BuiltinCodeIndexEngine", "PythonParser", "LuaParser", "GenericParser"):
        assert not hasattr(engine, name)


def test_generation_removes_unsupported_route_and_entrypoint_outputs(project):
    project.initialize_with_fake_codegraph()
    assert project.store.get_knowledge("generated.routes") is None
    assert project.store.get_knowledge("generated.entrypoints") is None
    assert not (project.generated_root / "routes.md").exists()
    assert not (project.generated_root / "entrypoints.md").exists()
```

Add a readonly mirror test that patches a real/fake CodeGraph client, verifies init/snapshot are called in the temporary mirror, and asserts the source tree hash is unchanged.

- [ ] **Step 2: Run focused tests and capture RED**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_engine.py tests/test_wp02_evidence.py tests/test_wp02_knowledge.py tests/test_evaluate.py -q`

Expected: parser symbols still exist and generated route/entrypoint records remain.

- [ ] **Step 3: Remove parser production code and parser-only tests**

Delete the builtin engine and all AST/Lua/generic parser classes from `engine.py`, including parser-only helper functions and imports. Delete `tests/test_engine_wp01_wp02.py`; do not move its parser expectations into another test.

Delete `ParseResult` and `Route` only after `rtk rg -n "ParseResult|Route" src tests` confirms no supported runtime consumer remains. Remove `KnowledgeStore.replace_file()` because Task 3 replaced it with snapshot storage.

- [ ] **Step 4: Remove unsupported generated records and files**

Change `KnowledgeGenerator.generate()` to generate the project map, file-based module maps, test file map, and reviewed knowledge only. Remove `_routes()` and `_entrypoints()`. Before writing the manifest, unlink the two obsolete generated files and delete their database records through `delete_missing_knowledge()`.

Update project-map wording to state that CodeGraph is authoritative and that exhaustive graph details are queried live. Remove claims about Python AST, regex extraction, local route inference, and Lua/Skynet entrypoint inference.

- [ ] **Step 5: Replace the readonly harness with CodeGraph mirror validation**

`inspect_readonly_scope()` may inspect file metadata only through `CodeGraphClient.snapshot()` on an initialized project. `run_readonly_mirror()` must copy allowed source files into a temporary directory, configure `engine: codegraph`, call CodeGraph init/snapshot/query checks there, and return Adapter diagnostics plus source revision evidence. Remove the `entrypoints` section from its report.

- [ ] **Step 6: Run focused tests and repository symbol scan**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_engine.py tests/test_wp02_evidence.py tests/test_wp02_knowledge.py tests/test_evaluate.py -q`

Run: `rtk rg -n "BuiltinCodeIndexEngine|PythonParser|LuaParser|GenericParser|_builtin_engine" src tests`

Expected: tests pass and `rg` returns no matches. Tests may contain the string `builtin` only for the explicit rejection/no-fallback contract.

- [ ] **Step 7: Commit the deletion**

```powershell
rtk git add -A src/project_knowledge/engine.py src/project_knowledge/models.py src/project_knowledge/store.py src/project_knowledge/knowledge.py src/project_knowledge/real_project.py tests/test_engine_wp01_wp02.py tests/test_wp02_evidence.py tests/test_wp02_knowledge.py tests/test_engine.py tests/test_evaluate.py
rtk git commit -m "refactor: remove builtin code parsers"
```

### Task 5: Route Runtime Retrieval Exclusively Through CodeGraph

**Requirements:** BE-003, BE-005, BE-006

**Files:**
- Modify: `src/project_knowledge/retrieval.py`
- Modify: `src/project_knowledge/mcp.py`
- Modify: `src/project_knowledge/evaluate.py`
- Modify: `tests/test_retrieval_wp06.py`
- Modify: `tests/test_integration.py`
- Modify: `tests/test_evaluate.py`
- Modify: `tests/test_codegraph.py`

**Interfaces:**
- Consumes: injected `ProjectService` and live engine query methods.
- Produces: code file candidates with `fact_source == "codegraph"`.
- Produces: structured Adapter failure for any code-fact request when CodeGraph is unavailable.
- Retains: grep only as the explicitly selected `grep_read` evaluation control strategy.

- [ ] **Step 1: Add failing stale-cache and provenance tests**

Seed legacy SQLite `symbols` and `relations` rows, configure a fake engine as unavailable, and assert `KnowledgeAPI.context()` raises `CodeGraphError` rather than returning those rows. With an available fake engine, assert every selected code candidate has `fact_source == "codegraph"` and the expected fake symbol/impact path.

Add a test that `strategy="grep_read"` still invokes grep explicitly, while `hybrid`, `code`, `context`, `impact`, and MCP tools never call it as fallback.

- [ ] **Step 2: Run focused tests and capture RED**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_retrieval_wp06.py tests/test_integration.py tests/test_evaluate.py tests/test_codegraph.py -q`

Expected: retrieval SQL reads legacy symbols/relations and at least one result reports builtin provenance.

- [ ] **Step 3: Remove local graph SQL from retrieval**

Delete all retrieval queries of the form `SELECT ... FROM symbols`, `SELECT ... FROM relations`, and `SELECT ... FROM routes`. Build symbol candidates from `engine.search_symbols()`, relation candidates from `engine.trace()`/`engine.impact()`, and affected tests from `engine.affected_tests()`.

Keep `KnowledgeStore` reads only for knowledge records, freshness, guidance, proposals, query stats, and file snapshot metadata. Do not infer symbol IDs from filenames when CodeGraph returns no symbol.

- [ ] **Step 4: Remove builtin provenance and automatic fallback**

Replace conditional fact sources with the literal `"codegraph"`. When an engine call fails, propagate its structured error and record the failed query statistic; do not call `fallback_rank_files`, grep, or a local store graph path. Ranking fallback inside an already successful CodeGraph candidate set remains governed by the separate WP-12A ranking contract and must not invent candidates.

- [ ] **Step 5: Align MCP and evaluator behavior**

MCP `context`, `impact`, and status results must expose Adapter reason codes. Evaluator `codegraph`/`code`/`hybrid` strategies must distinguish `adapter_unavailable` from a valid empty result and must never score stale SQLite facts as CodeGraph output.

- [ ] **Step 6: Run focused tests and SQL/provenance scan**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_retrieval_wp06.py tests/test_integration.py tests/test_evaluate.py tests/test_codegraph.py -q`

Run: `rtk rg -n "fact_source.*builtin|FROM symbols|FROM relations|FROM routes" src/project_knowledge/retrieval.py src/project_knowledge/mcp.py src/project_knowledge/evaluate.py`

Expected: focused tests pass; scan returns no runtime local graph reads or builtin provenance.

- [ ] **Step 7: Commit the live CodeGraph query path**

```powershell
rtk git add src/project_knowledge/retrieval.py src/project_knowledge/mcp.py src/project_knowledge/evaluate.py tests/test_retrieval_wp06.py tests/test_integration.py tests/test_evaluate.py tests/test_codegraph.py
rtk git commit -m "refactor: query code facts through CodeGraph"
```

### Task 6: Migrate The Remaining Test Suite To Explicit CodeGraph Facts

**Requirements:** BE-007, BE-009

**Files:**
- Modify: `tests/test_finalization.py`
- Modify: `tests/test_integration.py`
- Modify: `tests/test_proposal.py`
- Modify: `tests/test_retrieval_wp06.py`
- Modify: `tests/test_semantic.py`
- Modify: `tests/test_single_directory.py`
- Modify: `tests/test_watch_wp07.py`
- Modify: `tests/test_wp02_knowledge.py`
- Modify: `tests/test_wp08.py`
- Modify: `tests/test_evaluate.py`
- Modify: `tests/test_guidance_incremental.py`
- Modify: `tests/test_guidance_retrieval.py`

**Interfaces:**
- Consumes: `FakeCodeGraphEngine` and constructor injection from Tasks 2 and 3.
- Produces: deterministic tests that declare only the files, symbols, relations, sources, impacts, and affected tests needed by each assertion.

- [ ] **Step 1: Inventory tests still creating implicit engines**

Run: `rtk rg -n "ProjectService\(|KnowledgeAPI\(" tests`

For each listed test, classify it as file-snapshot only, code-query, knowledge-only, or real Adapter integration. Record the explicit fake data beside the test fixture; do not create a parser-based convenience fixture.

- [ ] **Step 2: Add fixture builders that declare facts**

Use small helpers such as:

```python
def snapshot_for(root: Path, *paths: str) -> CodeIndexSnapshot:
    files = tuple(indexed_file(root, path) for path in sorted(paths))
    digest = hashlib.sha256("\n".join(item.content_hash for item in files).encode()).hexdigest()
    return CodeIndexSnapshot(snapshot_id=digest, files=files)


def symbol(symbol_id: str, path: str, line: int = 1) -> Symbol:
    return Symbol(
        id=symbol_id, name=symbol_id.rsplit("::", 1)[-1], kind="function",
        path=path, line=line, end_line=None, signature="", source_hash="", confidence=1.0,
    )
```

`indexed_file()` may hash a named fixture file but must not inspect syntax or infer symbols.

- [ ] **Step 3: Migrate file-snapshot and knowledge-only tests**

Inject an engine with snapshots and empty code query mappings. Replace assertions about builtin symbol counts, local parser names, routes, and entrypoints with assertions about snapshot files, generated knowledge ownership, freshness, migration, and Adapter status.

- [ ] **Step 4: Migrate retrieval and semantic tests**

Declare exact symbol and relation mappings for each expected retrieval result. A test expecting `create_item` must include that `Symbol` explicitly; a call-path test must include its `Relation` explicitly. Do not derive facts from fixture source.

- [ ] **Step 5: Run the migrated test set and remove implicit dependencies**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_finalization.py tests/test_integration.py tests/test_proposal.py tests/test_retrieval_wp06.py tests/test_semantic.py tests/test_single_directory.py tests/test_watch_wp07.py tests/test_wp02_knowledge.py tests/test_wp08.py tests/test_evaluate.py tests/test_guidance_incremental.py tests/test_guidance_retrieval.py -q --junitxml=.project-kb/state/wp13-migrated-tests.xml`

Expected: all selected tests pass. Inspect the JUnit XML for exact counts and failures even if RTK suppresses the final pytest line. The complete suite runs in Task 9 after the delivery workflow and documentation tests are updated.

Run: `rtk rg -n "BuiltinCodeIndexEngine|PythonParser|LuaParser|GenericParser|engine: builtin" src tests`

Expected: no production/parser matches; the only allowed builtin text is the negative configuration/error test data.

- [ ] **Step 6: Commit the test migration**

```powershell
rtk git add tests
rtk git commit -m "test: use explicit CodeGraph engine fixtures"
```

### Task 7: Make CodeGraph A Mandatory Delivery Dependency

**Requirements:** BE-003, BE-007, BE-009

**Files:**
- Modify: `.github/workflows/quality.yml`
- Modify: `tests/test_delivery_reliability.py`
- Modify: `scripts/validate_ci_workflow.py`
- Modify: `scripts/validate_codegraph_adapter.py`
- Modify: `evaluation/thresholds.json` only if metadata must name 0.1.30; do not change metric limits.

**Interfaces:**
- Consumes: real Adapter validator from Task 2.
- Produces: CI structure that fails when CodeGraph validation fails.
- Produces: evaluation command without a nonexistent or failed release baseline.

- [ ] **Step 1: Write failing workflow structure tests**

Assert the quality workflow:

```python
assert "scripts/validate_codegraph_adapter.py" in workflow_text
assert "continue-on-error: true" not in codegraph_step
assert "evaluation/baselines/self-repo-0.1.29.json" not in workflow_text
assert "--thresholds evaluation/thresholds.json" in workflow_text
```

Also assert the workflow sets up CodeGraph before evaluation and runs `project-kb` from the checked-out source rather than an unrelated editable install.

- [ ] **Step 2: Run delivery tests and capture RED**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_delivery_reliability.py -q`

Expected: the current missing baseline reference and optional/absent Adapter installation fail the new contract.

- [ ] **Step 3: Update CI without weakening quality thresholds**

Install or restore the pinned CodeGraph version used by the repository, run the Adapter validation as a mandatory step, then run the 50-sample evaluation with thresholds and no `--baseline` until a clean passing 0.1.30 report exists. Do not add `continue-on-error`, sample exclusions, or lower limits.

Update the workflow validator to parse YAML structurally and verify step order: install CodeGraph, validate Adapter, run tests, run evaluation, run finalize check.

- [ ] **Step 4: Run CI structure and delivery tests**

Run: `rtk .\.venv\Scripts\python.exe scripts/validate_ci_workflow.py`

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_delivery_reliability.py -q`

Expected: both pass; no referenced baseline file is missing.

- [ ] **Step 5: Commit delivery enforcement**

```powershell
rtk git add .github/workflows/quality.yml tests/test_delivery_reliability.py scripts/validate_ci_workflow.py scripts/validate_codegraph_adapter.py evaluation/thresholds.json
rtk git commit -m "ci: require the real CodeGraph adapter"
```

### Task 8: Update Product Documentation And Bump Version Once

**Requirements:** BE-002, BE-004, BE-006, BE-008, BE-009

**Files:**
- Modify: `.project-kb.yml`
- Modify: `README.md`
- Modify: `docs/project-knowledge-system-audit.md`
- Modify: `docs/next-version-plan.md`
- Modify: `docs/knowledge/decisions/0001-local-first-core.md`
- Modify: `docs/knowledge/decisions/0002-codegraph-adapter-boundary.md`
- Modify: `docs/knowledge/decisions/0003-lua-skynet-entry-evidence.md`
- Modify: `docs/knowledge/curated/architecture.md`
- Modify: `docs/knowledge/curated/conventions.md`
- Modify: `docs/knowledge/index.md`
- Modify: `CHANGELOG.md`
- Modify through script: `src/project_knowledge/__init__.py`
- Test: `tests/test_documentation_roadmap.py`
- Test: `tests/test_delivery_reliability.py`

**Interfaces:**
- Consumes: completed behavior and evidence from Tasks 1-7.
- Produces: version `0.1.30`, one changelog record, WP-13 audit evidence, and CodeGraph-only user guidance.

- [ ] **Step 1: Add failing documentation assertions**

Assert current instructions and examples use `engine: codegraph`, current architecture does not describe builtin as available/default, ADR-0002 names CodeGraph as the sole authority, and the knowledge index omits generated routes/entrypoints links.

- [ ] **Step 2: Run documentation tests and capture RED**

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_documentation_roadmap.py tests/test_delivery_reliability.py -q`

Expected: current docs still advertise builtin and old generated outputs.

- [ ] **Step 3: Update config and user-facing documentation**

Change `.project-kb.yml` to `engine: codegraph`. Document CodeGraph installation/initialization as a prerequisite, the exact `unsupported_engine` migration message, removal of offline indexing, and failure behavior. Historical sections may say older releases used builtin only when version-scoped.

- [ ] **Step 4: Update audit, plan, and ADR decisions**

Add WP-13 with BE-001 through BE-009. Mark a requirement complete only when its tests and real Adapter evidence exist. Preserve WP-12A quality failures as open; do not attribute recall/precision improvements to engine removal without a current evaluation report.

Retire the previous decisions that kept builtin as default/offline implementation. ADR-0003 must state that Lua/Skynet entrypoint inference was removed because the CodeGraph public contract does not prove it.

- [ ] **Step 5: Bump the patch version exactly once**

Run: `rtk .\.venv\Scripts\python.exe scripts/bump_version.py "移除 builtin engine，CodeGraph 成为唯一代码事实源"`

Expected: `src/project_knowledge/__init__.py` reports `0.1.30` and `CHANGELOG.md` has one matching entry.

- [ ] **Step 6: Run version and documentation verification**

Set this shell for subsequent Project Knowledge commands:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
```

Run: `rtk .\.venv\Scripts\python.exe -m project_knowledge --version`

Expected: `project-kb 0.1.30`, and an import proof command prints a path under this worktree's `src/project_knowledge`.

Run: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_documentation_roadmap.py tests/test_delivery_reliability.py -q`

- [ ] **Step 7: Commit source documentation and version**

```powershell
rtk git add .project-kb.yml README.md docs/project-knowledge-system-audit.md docs/next-version-plan.md docs/knowledge/decisions docs/knowledge/curated docs/knowledge/index.md CHANGELOG.md src/project_knowledge/__init__.py tests/test_documentation_roadmap.py tests/test_delivery_reliability.py
rtk git commit -m "docs: release CodeGraph-only engine boundary"
```

### Task 9: Verify, Evaluate, Synchronize Knowledge, And Finalize

**Requirements:** BE-001 through BE-009

**Files:**
- Modify generated by commands: `.project-kb/manifest.json`
- Modify generated by commands: `docs/knowledge/generated/**`
- Create only if the absolute quality gate passes: `evaluation/baselines/self-repo-0.1.30.json`
- Do not commit as a baseline when failing: `evaluation/reports/wp13-0.1.30-absolute.json`

**Interfaces:**
- Consumes: all implementation tasks.
- Produces: reproducible test, Adapter, evaluation, version, knowledge synchronization, and finalization evidence.

- [ ] **Step 1: Prove the local import source and clean preconditions**

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
rtk .\.venv\Scripts\python.exe -c "import project_knowledge; print(project_knowledge.__file__); print(project_knowledge.__version__)"
rtk git status --short
```

Expected: import path is this worktree, version is `0.1.30`, and only intentional generated/evaluation outputs are present.

- [ ] **Step 2: Run focused boundary scans**

Run: `rtk rg -n "BuiltinCodeIndexEngine|PythonParser|LuaParser|GenericParser|_builtin_engine|fact_source.*builtin|engine == .builtin.|engine != .builtin." src tests .project-kb.yml`

Expected: no production match; only explicit negative test fixtures may contain the literal `builtin`.

- [ ] **Step 3: Run the full test suite with durable evidence**

Run: `rtk .\.venv\Scripts\python.exe -m pytest -q --junitxml=.project-kb/state/wp13-full-suite.xml`

Expected: zero failures and zero errors. Read the JUnit XML attributes to report the exact test count.

- [ ] **Step 4: Run real Adapter and CI validation**

Run: `rtk .\.venv\Scripts\python.exe scripts/validate_codegraph_adapter.py --json`

Run: `rtk .\.venv\Scripts\python.exe scripts/validate_ci_workflow.py`

Expected: Adapter report has `passed: true`; CI structure validation exits zero.

- [ ] **Step 5: Run the 50-sample absolute evaluation**

Run: `rtk .\.venv\Scripts\python.exe -m project_knowledge evaluate evaluation/questions.jsonl --project . --strategy all --thresholds evaluation/thresholds.json --output evaluation/reports/wp13-0.1.30-absolute.json --quiet`

Record every failed metric. If the gate fails, leave the report as evidence only and do not create or update a release baseline. If it passes with clean source/index metadata, copy that exact report to `evaluation/baselines/self-repo-0.1.30.json` and update CI in a separate reviewed commit.

- [ ] **Step 6: Commit all non-generated source changes before synchronization**

Run: `rtk git status --short`

If evaluation created a failing report under an ignored path, do not force-add it. Commit any missed non-generated implementation or documentation file before running sync so index commit alignment has a stable source commit.

- [ ] **Step 7: Synchronize generated knowledge from the source commit**

Run: `rtk .\.venv\Scripts\python.exe -m project_knowledge sync . --task-summary "WP-13 remove builtin engine and require CodeGraph" --json`

Review the changed generated files. Confirm obsolete generated routes/entrypoints files are removed and no generated document claims AST/regex/builtin facts.

- [ ] **Step 8: Commit synchronized knowledge separately**

```powershell
rtk git add .project-kb/manifest.json docs/knowledge/generated docs/knowledge/index.md
rtk git commit -m "docs: synchronize CodeGraph-only knowledge"
```

Do not run the version bump again.

- [ ] **Step 9: Run final read-only checks**

Run: `rtk .\.venv\Scripts\python.exe -m project_knowledge finalize . --check --json`

Run: `rtk .\.venv\Scripts\python.exe -m project_knowledge --version`

Run: `rtk git status --short --branch`

Expected: finalization truthfully reports `ready` or a specific curated-knowledge review requirement; version is `0.1.30`; worktree is clean. If curated knowledge remains stale, list each item and do not claim full finalization.

- [ ] **Step 10: Produce the completion report**

Report BE-001 through BE-009 status, exact full-suite count, real Adapter checks, absolute metrics, baseline decision, version, generated-knowledge synchronization, curated review state, finalization result, and remaining WP-12A quality failures. Do not use a report generated from another worktree or package import path.
