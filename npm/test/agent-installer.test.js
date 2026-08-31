"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  buildCodexBlock,
  buildPiExtension,
  installAgents,
  resolveAgentPaths,
  uninstallAgents,
} = require("../lib/agent-installer");

function tempDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "project-kb-agent-test-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test("resolveAgentPaths honors CODEX_HOME and PI_CODING_AGENT_DIR", () => {
  const paths = resolveAgentPaths({
    env: { CODEX_HOME: "C:\\Codex Home", PI_CODING_AGENT_DIR: "D:\\Pi Agent" },
    platform: "win32",
    homedir: "C:\\Users\\test",
  });
  assert.equal(paths.codexConfig, path.resolve("C:\\Codex Home", "config.toml"));
  assert.equal(paths.codexAgents, path.resolve("C:\\Codex Home", "AGENTS.md"));
  assert.equal(paths.piExtension, path.resolve("D:\\Pi Agent", "extensions", "project-kb.ts"));
});

test("global installer writes stable Codex config and Pi extension idempotently", (t) => {
  const root = tempDir(t);
  const env = {
    CODEX_HOME: path.join(root, "codex"),
    PI_CODING_AGENT_DIR: path.join(root, "pi"),
  };
  const first = installAgents({ target: ["codex", "pi"], location: "global", env, platform: "win32" });
  const second = installAgents({ target: ["codex", "pi"], location: "global", env, platform: "win32" });
  assert.deepEqual(second.changedPaths, []);
  const config = fs.readFileSync(first.paths.codexConfig, "utf8");
  assert.match(config, /command = "project-kb"/);
  assert.match(config, /args = \["mcp", "--project", "\."\]/);
  assert.doesNotMatch(config, /sys\.executable|CODEGRAPH_COMMAND|Scripts\\python/);
  assert.match(fs.readFileSync(first.paths.piExtension, "utf8"), /knowledge_context/);
});

test("global installer refreshes an older project-kb marker block", (t) => {
  const root = tempDir(t);
  const codexHome = path.join(root, "codex");
  fs.mkdirSync(codexHome, { recursive: true });
  const configPath = path.join(codexHome, "config.toml");
  fs.writeFileSync(configPath, [
    "model = \"gpt-5\"",
    "# project-kb:codex-mcp:start",
    "[mcp_servers.project_knowledge]",
    "command = \"C:/old/python.exe\"",
    "args = [\"-m\", \"project_knowledge\", \"mcp\"]",
    "# project-kb:codex-mcp:end",
    "",
  ].join("\n"));
  installAgents({ target: ["codex"], location: "global", env: { CODEX_HOME: codexHome }, platform: "win32" });
  const updated = fs.readFileSync(configPath, "utf8");
  assert.match(updated, /model = "gpt-5"/);
  assert.match(updated, /command = "project-kb"/);
  assert.doesNotMatch(updated, /old\/python\.exe/);
});

test("installer preserves user config and rejects unowned Codex server", (t) => {
  const root = tempDir(t);
  const codexHome = path.join(root, "codex");
  fs.mkdirSync(codexHome, { recursive: true });
  const configPath = path.join(codexHome, "config.toml");
  const original = '[mcp_servers.project_knowledge]\ncommand = "user-owned"\n';
  fs.writeFileSync(configPath, original);
  assert.throws(
    () => installAgents({ target: ["codex"], location: "global", env: { CODEX_HOME: codexHome }, platform: "win32" }),
    /project_knowledge/,
  );
  assert.equal(fs.readFileSync(configPath, "utf8"), original);
});

test("uninstall removes owned files but keeps unrelated Codex content", (t) => {
  const root = tempDir(t);
  const env = { CODEX_HOME: path.join(root, "codex"), PI_CODING_AGENT_DIR: path.join(root, "pi") };
  installAgents({ target: ["codex", "pi"], location: "global", env, platform: "win32" });
  fs.appendFileSync(path.join(env.CODEX_HOME, "config.toml"), '\nmodel = "gpt-5"\n');
  const result = uninstallAgents({ target: ["codex", "pi"], location: "global", env, platform: "win32" });
  assert.ok(result.removedPaths.length >= 2);
  assert.match(fs.readFileSync(path.join(env.CODEX_HOME, "config.toml"), "utf8"), /model = "gpt-5"/);
  assert.equal(fs.existsSync(path.join(env.PI_CODING_AGENT_DIR, "extensions", "project-kb.ts")), false);
});

test("Pi extension uses cwd and stable launcher", () => {
  const extension = buildPiExtension();
  assert.match(extension, /process\.cwd\(\)/);
  assert.match(extension, /project-kb(?:\.cmd)?/);
  assert.match(extension, /getAllTools/);
});
