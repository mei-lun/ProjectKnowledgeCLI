# NPM Bootstrap Implementation Plan

**Goal:** Deliver `npm install --global project-kb-cli` followed by `project-kb init` as the Windows-first installation and project initialization path.

**Architecture:** A thin Node launcher owns Python runtime discovery and a versioned managed virtual environment. The Python package remains the product implementation and writes an owned Codex MCP block only after successful project initialization.

**Tech Stack:** Node.js 20+, npm 10+, Python 3.11+, `venv`, stdlib `tomllib`, `unittest`, MCP stdio JSON-RPC, CodeGraph 1.5.0.

**Spec:** `docs/superpowers/specs/2026-08-24-npm-bootstrap-design.md`

## Global Constraints

- Work package: `WP-NPM-01`; requirements: `NPM-001` through `NPM-007`.
- Target release: `0.1.48`; the only version source remains `src/project_knowledge/__init__.py`.
- Keep the Python CLI and MCP tool behavior stable; Node only bootstraps and forwards.
- Windows 10/11 x64 is the release gate. Python 3.11+ remains a user prerequisite.
- Pin `@colbymchenry/codegraph` to `1.5.0` in the generated npm package.
- Add positive and negative tests before each production change.
- Run repository commands through `rtk`.
- Bump the patch version exactly once after implementation, documentation, and evaluation are complete.

## Task 1: Codex Project Integration Tests (`NPM-004`, `NPM-005`)

**Files:**

- Create: `tests/test_codex_bootstrap.py`
- Modify: `src/project_knowledge/service.py`
- Modify: `src/project_knowledge/util.py` only if a reusable marker helper is required

1. Add tests proving successful `init` writes owned `AGENTS.md` and `.codex/config.toml` blocks.
2. Add tests proving dry-run lists both targets without writing.
3. Add tests proving CodeGraph/rebuild failure writes neither Codex integration target.
4. Add tests for idempotency, complete TOML validation, unowned-table conflict, and uninstall preservation.
5. Run the focused tests and confirm they fail for the expected missing behavior.
6. Implement the smallest Codex integration writer and rerun focused tests.

## Task 2: Node Runtime Bootstrap Tests (`NPM-001`, `NPM-002`, `NPM-003`)

**Files:**

- Create: `npm/package.template.json`
- Create: `npm/bin/project-kb.js`
- Create: `npm/lib/runtime.js`
- Create: `npm/scripts/postinstall.js`
- Create: `npm/test/runtime.test.js`

1. Add Node tests for Python discovery order, runtime root selection, completed-runtime reuse, failed setup cleanup, lock contention, and argument/exit-code forwarding.
2. Run `node --test npm/test/*.test.js` and confirm expected failures.
3. Implement the versioned venv bootstrap, offline wheel install, completion marker, lock, and transparent launcher.
4. Set `CODEGRAPH_COMMAND` to the npm-owned pinned executable unless the caller supplied an override.
5. Rerun the Node tests.

## Task 3: Reproducible NPM Package Build (`NPM-006`)

**Files:**

- Create: `scripts/build_npm_package.py`
- Create: `tests/test_npm_package_build.py`
- Modify: `.gitignore` if generated staging needs an ignored location

1. Add tests proving the build reads the Python version source, rejects mismatched wheels, and emits a package manifest without another committed version.
2. Implement wheel creation and npm staging generation under `dist/npm-package`.
3. Verify `npm pack --dry-run` includes the launcher, runtime helper, postinstall script, and exactly one matching wheel.

## Task 4: Windows Installed-Package MCP Evaluation (`NPM-007`)

**Files:**

- Create: `scripts/validate_npm_bootstrap.py`
- Create: `tests/test_npm_bootstrap_validation.py`
- Modify: `.github/workflows/quality.yml`

1. Add a validator test using an isolated npm prefix and runtime home.
2. Build and pack the npm artifact, install the tarball, and run `project-kb --version`.
3. Initialize a temporary Git repository with the installed launcher and pinned CodeGraph.
4. Validate `.codex/config.toml`, repeat `init`, and perform MCP `initialize`, `tools/list`, and `knowledge_status` over stdio.
5. Run uninstall and verify only owned blocks are removed.
6. Add a Windows CI job that executes the same validator.

## Task 5: Documentation, Audit, Version, and Knowledge Finalization

**Files:**

- Modify: `README.md`
- Modify: `docs/compatibility-matrix.md`
- Modify: `docs/project-knowledge-system-audit.md`
- Modify: `CHANGELOG.md` through the version script
- Modify: `src/project_knowledge/__init__.py` through the version script
- Synchronize: generated project knowledge outputs

1. Document the two-command installation path, prerequisites, runtime location, upgrade behavior, and Codex restart requirement.
2. Record `WP-NPM-01 / NPM-001..007` evidence in the audit only after all gates pass.
3. Run `python scripts/bump_version.py "新增 npm 一键安装与 Codex 项目初始化"` exactly once.
4. Verify `python -m project_knowledge --version` and the matching changelog entry.
5. Run focused Python and Node tests, the installed-package validator, and the full Python suite.
6. Synchronize generated knowledge and report whether curated knowledge needs review.
7. Perform a final diff/security review; no delegated reviewer is used unless the user explicitly authorizes a subagent.
