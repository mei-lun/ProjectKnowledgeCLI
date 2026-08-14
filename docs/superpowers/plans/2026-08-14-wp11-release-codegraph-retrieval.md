# WP-11 Release, CodeGraph, and Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver 0.1.27 with a deterministic release-finalization workflow, a real CodeGraph-backed query path, and materially higher retrieval precision without lowering recall gates.

**Architecture:** Add a small `FinalizationService` over existing Git/status/sync behavior; normalize CodeGraph 1.5 public CLI responses inside `CodeGraphEngine`; route `KnowledgeAPI` through the selected engine; then tighten evaluation retrieval and freeze new precision gates. SQLite remains the knowledge and compatibility cache, while `engine=codegraph` uses CodeGraph as the live symbol/relationship authority.

**Tech Stack:** Python 3.11+, stdlib `unittest`, SQLite, Git CLI, CodeGraph 1.5 public CLI, GitHub Actions YAML.

**Spec:** `docs/superpowers/specs/2026-08-14-release-codegraph-retrieval-design.md`

## Global Constraints

- The version is already `0.1.27`; do not bump it again in this batch.
- Do not read CodeGraph private databases or silently fall back from `engine=codegraph` to builtin facts.
- Do not execute `git add`, `git commit`, `git push`, or delete user files from `project-kb finalize`.
- Write each behavior test first and observe the expected failure before changing production code.
- Keep existing recall/success thresholds; add precision thresholds only after current anchors are corrected.
- Real CodeGraph validation must use a temporary fixture and must not create `.codegraph` in the source repository.
- Generated knowledge is synchronized only after source implementation and documentation are complete.

## Requirement Coverage

| Requirements | Implemented and verified by |
| --- | --- |
| REL-001, REL-002, REL-003, REL-004, REL-005 | Task 1, with final proof in Task 7 |
| CG-001, CG-002, CG-004 | Task 2 |
| CG-003 | Task 3 |
| CG-006 | Task 4 |
| RET-001, RET-002, RET-003, RET-004, RET-005 | Task 5 |
| CG-005, RET-006 | Task 6, with release evidence in Task 7 |
| Version, audit, curated review, generated synchronization | Task 7 |

---

### Task 1: Release finalization state machine

**Files:**
- Create: `src/project_knowledge/finalization.py`
- Create: `tests/test_finalization.py`
- Modify: `src/project_knowledge/cli.py`
- Modify: `.github/workflows/quality.yml`

**Interfaces:**
- Consumes: `ProjectService.status()`, `ProjectService.sync()`, `ProjectService._is_generated_output(path)`.
- Produces: `FinalizationService.finalize(check_only: bool = False) -> tuple[dict[str, Any], bool]` and CLI `project-kb finalize [project] [--check] [--json]`.

- [ ] **Step 1: Write failing end-to-end release tests**

```python
class FinalizationTests(unittest.TestCase):
    def test_source_commit_sync_generated_commit_becomes_ready(self):
        service, root = initialized_git_project()
        commit_all(root, "source")
        first, ok = FinalizationService(root).finalize()
        self.assertFalse(ok)
        self.assertEqual(first["status"], "generated_commit_required")
        self.assertTrue(first["generated_files"])
        commit_all(root, "generated")
        final, ok = FinalizationService(root).finalize(check_only=True)
        self.assertTrue(ok)
        self.assertEqual(final["status"], "ready")

    def test_check_only_never_writes_when_sync_is_required(self):
        service, root = initialized_git_project()
        commit_all(root, "source")
        before = snapshot_tree(root)
        result, ok = FinalizationService(root).finalize(check_only=True)
        self.assertFalse(ok)
        self.assertEqual(result["status"], "sync_required")
        self.assertEqual(snapshot_tree(root), before)

    def test_non_generated_worktree_changes_require_source_commit(self):
        service, root = initialized_git_project()
        (root / "src/app.py").write_text("VALUE = 2\n", encoding="utf-8")
        result, ok = FinalizationService(root).finalize()
        self.assertFalse(ok)
        self.assertEqual(result["status"], "source_commit_required")
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_finalization -v`  
Expected: FAIL because `project_knowledge.finalization` does not exist.

- [ ] **Step 3: Implement the minimal state machine**

```python
class FinalizationService:
    def __init__(self, project: str | Path = "."):
        self.service = ProjectService(project)

    def finalize(self, check_only: bool = False) -> tuple[dict[str, Any], bool]:
        before = self.service.status()
        dirty = git_changed_paths(self.service.root)
        non_generated = [path for path in dirty if not self.service._is_generated_output(path)]
        if non_generated:
            return self._result("source_commit_required", before, blocking_files=non_generated), False
        if not before["verification_aligned"]:
            if check_only:
                return self._result("sync_required", before), False
            self.service.sync(task_summary="release finalization")
        after = self.service.status()
        generated = [path for path in git_changed_paths(self.service.root) if self.service._is_generated_output(path)]
        if generated:
            return self._result("generated_commit_required", after, generated_files=generated), False
        healthy = after["verification_aligned"] and not after["counts"]["stale_knowledge"] and not after["counts"]["conflicted_knowledge"]
        return self._result("ready" if healthy else "knowledge_review_required", after), healthy
```

Use `run_git(root, "status", "--porcelain=v1", "-z")` and parse rename records without shell interpolation. Return `head_commit`, `index_commit`, `verification_aligned`, `blocking_files`, `generated_files`, and `next_action` in every result.

- [ ] **Step 4: Add CLI behavior and CI structural assertion**

Add a `finalize` subparser beside `check`, pass `--check` to `FinalizationService`, print JSON with the existing CLI serializer, and return exit code `0` only for `ready`. Update the workflow after evaluation to run:

```yaml
- name: 验证发布收尾状态
  run: project-kb finalize . --check --json
```

Extend `tests/test_delivery_reliability.py` so `validate_ci_workflow.py` requires this command.

- [ ] **Step 5: Run targeted tests and commit**

Run: `.venv\Scripts\python.exe -m unittest tests.test_finalization tests.test_delivery_reliability tests.test_integration -v`  
Expected: PASS.

Commit:

```powershell
git add src/project_knowledge/finalization.py src/project_knowledge/cli.py tests/test_finalization.py tests/test_delivery_reliability.py .github/workflows/quality.yml
git commit -m "feat: add deterministic release finalization"
```

---

### Task 2: CodeGraph 1.5 contract normalization and diagnostics

**Files:**
- Modify: `src/project_knowledge/codegraph.py`
- Modify: `src/project_knowledge/engine.py`
- Modify: `src/project_knowledge/service.py`
- Modify: `tests/test_codegraph.py`
- Modify: `tests/test_integration.py`

**Interfaces:**
- Consumes: `CodeGraphClient.status/files/query/callers/callees/impact/affected_tests/source`.
- Produces: normalized `CodeGraphEngine.search_symbols/trace/impact/affected_tests` matching `CodeIndexEngine`, plus side-effect-free `CodeGraphEngine.diagnose(root: Path) -> dict[str, Any]`.

- [ ] **Step 1: Write failing normalization and failure tests**

```python
def test_engine_normalizes_codegraph_impact_contract(self):
    engine = engine_with_client(FakeCodeGraphClient(impact={
        "symbol": "login",
        "affected": [{"id": "route", "name": "route", "filePath": "src/router.lua", "startLine": 2}],
    }))
    result = engine.impact(root, config, symbols=["src/app.lua::login"], max_hops=2)
    self.assertEqual(result["affected_files"], ["src/router.lua"])
    self.assertEqual(result["affected_symbols"], ["route"])
    self.assertEqual(result["relations"][0]["source"], "src/app.lua::login")
    self.assertEqual(result["relations"][0]["target"], "route")

def test_status_reports_uninitialized_reason_without_builtin_fallback(self):
    engine = engine_with_client(FakeCodeGraphClient(status={"initialized": False, "version": "1.5.0"}))
    status = engine.diagnose(root)
    self.assertFalse(status["available"])
    self.assertEqual(status["reason_code"], "project_not_initialized")
    self.assertEqual(status["adapter_version"], "1.5.0")
```

Add negative cases for missing CLI, timeout, invalid JSON, project-external paths, missing symbol identity, and nonzero exit.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_codegraph -v`  
Expected: FAIL because `impact()` returns raw CodeGraph JSON and status lacks normalized reason/capabilities.

- [ ] **Step 3: Normalize the public contract**

Implement focused helpers:

```python
def _node_identity(node: dict[str, Any]) -> str:
    value = node.get("id") or node.get("qualifiedName") or node.get("name")
    if not value:
        raise CodeGraphError("CodeGraph node missing identity")
    return str(value)

def _node_path(node: dict[str, Any], root: Path) -> str:
    path = str(node.get("filePath", node.get("path", ""))).replace("\\", "/")
    return _validated_project_path(path, root)
```

Normalize `impact()` to the engine contract and derive modules with `_module_for`. Normalize callers/callees to `Relation` objects. `diagnose(root)` must resolve the CLI, call public `status`, report `adapter_version`, fixed capabilities, limitations, and one of `cli_missing`, `project_not_initialized`, `command_failed`, or `available`. Keep parameterless `status()` for the existing engine capability contract and have it return the most recent diagnostic when available.

- [ ] **Step 4: Surface diagnostics through doctor/status**

Make `ProjectService.doctor()` call a side-effect-free engine diagnostic. `ProjectService.status()` must not claim CodeGraph available merely because a client object was constructed.

- [ ] **Step 5: Run targeted tests and commit**

Run: `.venv\Scripts\python.exe -m unittest tests.test_codegraph tests.test_engine_wp01_wp02 tests.test_integration -v`  
Expected: PASS.

Commit:

```powershell
git add src/project_knowledge/codegraph.py src/project_knowledge/engine.py src/project_knowledge/service.py tests/test_codegraph.py tests/test_integration.py
git commit -m "feat: normalize CodeGraph adapter contract"
```

---

### Task 3: Route KnowledgeAPI through the selected engine

**Files:**
- Modify: `src/project_knowledge/retrieval.py`
- Modify: `tests/test_retrieval_wp06.py`
- Modify: `tests/test_codegraph.py`

**Interfaces:**
- Consumes: normalized `CodeIndexEngine.search_symbols()` and `CodeIndexEngine.impact()` from Task 2.
- Produces: `KnowledgeAPI._task_symbol_matches(task, terms)`, engine-backed `context()`, and engine-backed `impact()` for CodeGraph projects.

- [ ] **Step 1: Write a failing main-path test with an empty SQLite graph**

```python
def test_codegraph_context_uses_engine_when_sqlite_symbols_are_empty(self):
    api = initialized_api(engine="codegraph", fake_client=client_with_login_graph())
    with KnowledgeStore(api.service.db_path) as store:
        store.connection.execute("DELETE FROM relations")
        store.connection.execute("DELETE FROM symbols")
        store.connection.commit()
    result = api.context("修改 login 路由", max_tokens=2000)
    self.assertIn("src/app.lua::login", {item["id"] for item in result["symbols"]})
    self.assertIn("src/router.lua", result["impact"]["affected_files"])
    self.assertEqual(result["fact_source"], "codegraph")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_retrieval_wp06 tests.test_codegraph -v`  
Expected: FAIL because `context()` and `impact()` query SQLite directly.

- [ ] **Step 3: Add engine routing without changing builtin behavior**

```python
def _task_symbol_matches(self, task: str, terms: list[str]) -> list[dict[str, Any]]:
    if self.config.engine == "codegraph":
        matches = []
        for term in terms[:8]:
            matches.extend(symbol.to_dict() for symbol in self.service.engine.search_symbols(self.root, self.config, term, limit=3))
        return stable_unique(matches, key="id")[:12]
    return self._store_symbol_matches(terms)
```

In `impact()`, delegate to `self.service.engine.impact(...)` for CodeGraph and attach relevant knowledge by intersecting normalized affected files/symbols with record sources. Preserve the existing SQLite implementation for builtin. Add `fact_source` and engine limitations to context/impact responses.

- [ ] **Step 4: Run targeted tests and commit**

Run: `.venv\Scripts\python.exe -m unittest tests.test_retrieval_wp06 tests.test_codegraph tests.test_integration -v`  
Expected: PASS.

Commit:

```powershell
git add src/project_knowledge/retrieval.py tests/test_retrieval_wp06.py tests/test_codegraph.py
git commit -m "feat: use CodeGraph facts in knowledge queries"
```

---

### Task 4: Enable real CodeGraph evaluation

**Files:**
- Modify: `src/project_knowledge/evaluate.py`
- Modify: `tests/test_evaluate.py`

**Interfaces:**
- Consumes: engine availability and `KnowledgeAPI.context()` from Tasks 2–3.
- Produces: dynamic `strategy=codegraph` evaluation with truthful `adapter_unavailable` reasons.

- [ ] **Step 1: Write failing evaluation tests**

```python
def test_codegraph_strategy_evaluates_when_adapter_is_available(self):
    report = evaluate(codegraph_project, dataset, strategy="codegraph")
    self.assertTrue(report["available"])
    self.assertEqual(report["strategy"], "codegraph")
    self.assertEqual(report["reproducibility"]["engine"]["engine"], "codegraph")

def test_codegraph_strategy_reports_probe_reason(self):
    report = evaluate(builtin_project, dataset, strategy="codegraph")
    self.assertFalse(report["available"])
    self.assertEqual(report["reason_code"], "adapter_unavailable")
    self.assertIn("engine_not_selected", report["details"])
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_evaluate -v`  
Expected: FAIL because lines 94–101 hard-code unavailable.

- [ ] **Step 3: Implement the dynamic strategy**

Construct `KnowledgeAPI` before deciding availability. For `strategy=codegraph`, require `config.engine == "codegraph"` and `engine.diagnose(api.root)["available"]`; then reuse the code retrieval path while labeling the report `codegraph`. Preserve structured unavailable reports for builtin projects and unavailable adapters.

- [ ] **Step 4: Run targeted tests and commit**

Run: `.venv\Scripts\python.exe -m unittest tests.test_evaluate tests.test_codegraph -v`  
Expected: PASS.

Commit:

```powershell
git add src/project_knowledge/evaluate.py tests/test_evaluate.py
git commit -m "feat: evaluate the real CodeGraph strategy"
```

---

### Task 5: Correct evaluation anchors and bound retrieval expansion

**Files:**
- Modify: `evaluation/questions.jsonl`
- Modify: `evaluation/thresholds.json`
- Modify: `src/project_knowledge/evaluate.py`
- Modify: `src/project_knowledge/retrieval.py`
- Modify: `tests/test_evaluate.py`
- Modify: `tests/test_retrieval_wp06.py`

**Interfaces:**
- Consumes: engine-backed context and evaluation from Tasks 3–4.
- Produces: bounded file selection with `selection_reasons` and new precision gates.

- [ ] **Step 1: Correct stale anchors and add failing precision tests**

Change `self-codegraph-failure.expected_files` to include `src/project_knowledge/codegraph.py` and `docs/knowledge/decisions/0002-codegraph-adapter-boundary.md`, and add the relevant CodeGraph symbol IDs.

```python
def test_hybrid_does_not_expand_knowledge_sources_twice(self):
    result = _retrieve(api, sample, "hybrid")
    self.assertLessEqual(len(result["files"]), 20)
    self.assertIn("src/project_knowledge/retrieval.py", result["files"])
    self.assertTrue(all(path in result["selection_reasons"] for path in result["files"]))

def test_markdown_source_paths_are_adaptively_bounded(self):
    paths = _rank_markdown_source_paths(api, results, task, limit=8)
    self.assertLessEqual(len(paths), 8)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_evaluate tests.test_retrieval_wp06 -v`  
Expected: FAIL because hybrid returns the second impact expansion and Markdown permits 23 paths.

- [ ] **Step 3: Implement staged selection**

Keep separate sets for direct symbol files, one-hop impact files, directly intersecting knowledge sources, and fallback files. Do not add knowledge source IDs to the second impact call; remove the second call entirely when context already contains impact. Add a deterministic reason map:

```python
selection_reasons[path] = {
    "stage": "direct_symbol" | "impact" | "knowledge_source" | "fallback",
    "anchor": symbol_or_record_id,
}
```

Limit direct symbols to 12, impact files to 12, intersecting knowledge sources to 8, and Markdown ranked sources to 8. Preserve an expected file over a lower-ranked unrelated candidate only through generic scoring, never by reading evaluation gold fields.

- [ ] **Step 4: Freeze precision gates after running the corrected dataset**

Add to `evaluation/thresholds.json`:

```json
"hybrid": {"minimum": {"file_precision": 0.12}},
"code": {"minimum": {"file_precision": 0.20}},
"markdown": {"minimum": {"file_precision": 0.12}}
```

Merge these keys into the existing per-strategy minimum objects without changing existing floors.

- [ ] **Step 5: Run the full offline evaluation and commit**

Run:

```powershell
.venv\Scripts\python.exe -m project_knowledge evaluate evaluation\questions.jsonl --project . --strategy all --thresholds evaluation\thresholds.json --baseline evaluation\baselines\self-repo-0.1.26.json --output evaluation\reports\latest.json --quiet
```

Expected: quality gate passes; hybrid average returned files are at most 20.475; recall and success floors remain satisfied.

Commit:

```powershell
git add evaluation/questions.jsonl evaluation/thresholds.json evaluation/reports/latest.json src/project_knowledge/evaluate.py src/project_knowledge/retrieval.py tests/test_evaluate.py tests/test_retrieval_wp06.py
git commit -m "fix: improve retrieval precision with bounded evidence"
```

---

### Task 6: Real CodeGraph 1.5 validation harness

**Files:**
- Create: `scripts/validate_codegraph_adapter.py`
- Create: `tests/test_codegraph_validation.py`
- Modify: `docs/evaluation-guide.md`

**Interfaces:**
- Consumes: installed `codegraph` public CLI and `CodeGraphEngine`.
- Produces: JSON validation report and exit code 0 only when init/files/query/trace/impact/affected pass on a temporary fixture.

- [ ] **Step 1: Write a failing harness test with an injected command**

```python
def test_validation_uses_temporary_project_and_cleans_it(self):
    report = validate_codegraph(command=fake_cli, keep_fixture=False)
    self.assertTrue(report["passed"])
    self.assertEqual(report["adapter_version"], "1.5.0")
    self.assertEqual(report["checks"], ["init", "files", "query", "trace", "impact", "affected"])
    self.assertFalse(Path(report["fixture_path"]).exists())
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_codegraph_validation -v`  
Expected: FAIL because the script/module does not exist.

- [ ] **Step 3: Implement the temporary integration harness**

Create a temporary Python/Lua fixture with a caller, callee, test, configuration reference, and Lua service entry. Run `codegraph init`, then validate public commands through `CodeGraphClient`/`CodeGraphEngine`. Record command, version, durations, normalized counts, and failure reason. Never copy or initialize the source repository.

- [ ] **Step 4: Run fake and real validation, then commit**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_codegraph_validation tests.test_codegraph -v
.venv\Scripts\python.exe scripts\validate_codegraph_adapter.py --json
```

Expected: both commands pass; real report identifies CodeGraph 1.5.0.

Commit:

```powershell
git add scripts/validate_codegraph_adapter.py tests/test_codegraph_validation.py docs/evaluation-guide.md
git commit -m "test: validate the real CodeGraph adapter"
```

---

### Task 7: Audit, curated review, generated knowledge, and final release proof

**Files:**
- Modify: `README.md`
- Modify: `docs/project-knowledge-system-audit.md`
- Modify: `docs/next-version-plan.md`
- Modify: `docs/knowledge/decisions/0002-codegraph-adapter-boundary.md`
- Modify: the seven stale curated/decision files only where live source confirms their claims
- Modify: `CHANGELOG.md`
- Modify: generated knowledge and manifest outputs produced by `project-kb sync`

**Interfaces:**
- Consumes: all REL/CG/RET implementation and verification evidence.
- Produces: current audit, user documentation, reviewed curated records, synchronized generated knowledge, and finalization proof.

- [ ] **Step 1: Add failing documentation assertions**

Extend `tests/test_documentation_roadmap.py` and `tests/test_delivery_reliability.py` to require:

```python
self.assertIn("WP-11", audit)
self.assertIn("REL-001", audit)
self.assertIn("CG-006", audit)
self.assertIn("RET-006", audit)
self.assertNotIn("真实 CodeGraph Adapter 仍未完成", current_audit_section)
self.assertIn("project-kb finalize", readme)
```

- [ ] **Step 2: Run documentation tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_documentation_roadmap tests.test_delivery_reliability -v`  
Expected: FAIL because the current docs still describe WP-10 and unavailable CodeGraph.

- [ ] **Step 3: Update current documentation and review stale knowledge**

Update only the current audit section; retain historical sections with an explicit historical label. For each stale record, compare every source anchor against live files, update true claims, remove obsolete claims, and preserve `potentially_stale` when business ownership cannot be confirmed. Do not mark an item complete based only on schema fields or mock tests.

- [ ] **Step 4: Run full verification before synchronization**

Run:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe scripts\validate_ci_workflow.py
.venv\Scripts\python.exe scripts\validate_codegraph_adapter.py --json
.venv\Scripts\python.exe -m project_knowledge --version
git diff --check
```

Expected: all tests pass, CodeGraph validation passes, version is 0.1.27, and diff check is clean.

- [ ] **Step 5: Commit source/docs, synchronize, and create the generated-only commit**

Commit all source, tests, evaluation, and manually reviewed docs first. Then run:

```powershell
.venv\Scripts\python.exe -m project_knowledge finalize . --json
```

Review `generated_files`, commit only PKS-owned generated outputs, and run:

```powershell
.venv\Scripts\python.exe -m project_knowledge finalize . --check --json
.venv\Scripts\python.exe -m project_knowledge check . --json
```

Expected: finalization status is `ready`, `verification_aligned=true`, no pending files, no stale/conflicted knowledge, and the working tree is clean.

- [ ] **Step 6: Final full verification**

Re-run the full unit suite, offline evaluation, real CodeGraph validation, version check, `git diff --check`, `git status --short`, and inspect the final two commits. Do not claim completion if any command is stale or failed.
