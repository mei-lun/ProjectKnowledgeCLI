"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  buildLaunchSpec,
  discoverPython,
  ensureRuntime,
  runtimeBase,
} = require("../lib/runtime");


function temporaryDirectory(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "project-kb-node-test-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}


test("discoverPython respects override then falls back to a compatible interpreter", () => {
  const calls = [];
  const spawnSyncImpl = (command, args) => {
    calls.push([command, args]);
    if (command === "C:\\Python312\\python.exe") {
      return { status: 0, stdout: "3.12.4\n", stderr: "" };
    }
    return { status: 1, stdout: "", stderr: "missing" };
  };

  const selected = discoverPython({
    env: { PROJECT_KB_PYTHON: "C:\\Python312\\python.exe" },
    platform: "win32",
    spawnSyncImpl,
  });

  assert.equal(selected.command, "C:\\Python312\\python.exe");
  assert.deepEqual(selected.argsPrefix, []);
  assert.equal(selected.version, "3.12.4");
  assert.equal(calls.length, 1);
});


test("discoverPython rejects old versions and probes the documented order", () => {
  const calls = [];
  const spawnSyncImpl = (command, args) => {
    calls.push([command, args]);
    if (command === "py") return { status: 1, stdout: "", stderr: "missing 3.11" };
    if (command === "python") return { status: 0, stdout: "3.10.14\n", stderr: "" };
    if (command === "python3") return { status: 0, stdout: "3.13.2\n", stderr: "" };
    return { status: 1, stdout: "", stderr: "missing" };
  };

  const selected = discoverPython({ env: {}, platform: "win32", spawnSyncImpl });

  assert.equal(selected.command, "python3");
  assert.deepEqual(calls.map(([command]) => command), ["py", "python", "python3"]);
  assert.deepEqual(calls[0][1].slice(0, 1), ["-3.11"]);
});


test("runtimeBase uses an explicit testable override and Windows fallback", () => {
  assert.equal(
    runtimeBase({ env: { PROJECT_KB_RUNTIME_HOME: "D:\\runtimes" }, platform: "win32" }),
    path.resolve("D:\\runtimes"),
  );
  assert.equal(
    runtimeBase({ env: { LOCALAPPDATA: "C:\\Users\\mei\\AppData\\Local" }, platform: "win32" }),
    path.resolve("C:\\Users\\mei\\AppData\\Local", "ProjectKnowledgeCLI", "runtimes"),
  );
});


test("ensureRuntime installs once, writes completion marker, and reuses it", (t) => {
  const root = temporaryDirectory(t);
  const packageRoot = path.join(root, "package");
  const runtimeHome = path.join(root, "runtimes");
  fs.mkdirSync(path.join(packageRoot, "vendor"), { recursive: true });
  fs.writeFileSync(path.join(packageRoot, "vendor", "project_knowledge_cli-0.1.48-py3-none-any.whl"), "wheel");
  const calls = [];
  const spawnSyncImpl = (command, args) => {
    calls.push([command, args]);
    if (args[0] === "-m" && args[1] === "venv") {
      const target = args.at(-1);
      fs.mkdirSync(path.join(target, "Scripts"), { recursive: true });
      fs.writeFileSync(path.join(target, "Scripts", "python.exe"), "python");
    }
    return { status: 0, stdout: "", stderr: "" };
  };
  const options = {
    packageRoot,
    packageVersion: "0.1.48",
    env: { PROJECT_KB_RUNTIME_HOME: runtimeHome },
    platform: "win32",
    python: { command: "python", argsPrefix: [], version: "3.12.4" },
    spawnSyncImpl,
  };

  const first = ensureRuntime(options);
  const second = ensureRuntime(options);

  assert.equal(first, second);
  assert.equal(calls.length, 2);
  assert.ok(fs.existsSync(path.join(runtimeHome, "0.1.48", ".complete")));
  assert.match(calls[1][1].join(" "), /--no-index/);
  assert.match(calls[1][1].join(" "), /project_knowledge_cli-0\.1\.48/);
});


test("ensureRuntime removes partial setup after a failed command", (t) => {
  const root = temporaryDirectory(t);
  const packageRoot = path.join(root, "package");
  const runtimeHome = path.join(root, "runtimes");
  fs.mkdirSync(path.join(packageRoot, "vendor"), { recursive: true });
  fs.writeFileSync(path.join(packageRoot, "vendor", "project_knowledge_cli-0.1.48-py3-none-any.whl"), "wheel");

  assert.throws(
    () => ensureRuntime({
      packageRoot,
      packageVersion: "0.1.48",
      env: { PROJECT_KB_RUNTIME_HOME: runtimeHome },
      platform: "win32",
      python: { command: "python", argsPrefix: [], version: "3.12.4" },
      spawnSyncImpl: () => ({ status: 7, stdout: "", stderr: "venv failed" }),
    }),
    /venv failed/,
  );
  assert.equal(fs.existsSync(path.join(runtimeHome, "0.1.48")), false);
  assert.deepEqual(fs.readdirSync(runtimeHome), []);
});


test("ensureRuntime reports lock contention without damaging the owner", (t) => {
  const root = temporaryDirectory(t);
  const packageRoot = path.join(root, "package");
  const runtimeHome = path.join(root, "runtimes");
  fs.mkdirSync(path.join(packageRoot, "vendor"), { recursive: true });
  fs.mkdirSync(runtimeHome, { recursive: true });
  fs.writeFileSync(path.join(packageRoot, "vendor", "project_knowledge_cli-0.1.48-py3-none-any.whl"), "wheel");
  fs.writeFileSync(path.join(runtimeHome, "0.1.48.lock"), "owner");

  assert.throws(
    () => ensureRuntime({
      packageRoot,
      packageVersion: "0.1.48",
      env: { PROJECT_KB_RUNTIME_HOME: runtimeHome },
      platform: "win32",
      python: { command: "python", argsPrefix: [], version: "3.12.4" },
      spawnSyncImpl: () => ({ status: 0, stdout: "", stderr: "" }),
      lockTimeoutMs: 0,
    }),
    /another project-kb setup is running/,
  );
  assert.equal(fs.readFileSync(path.join(runtimeHome, "0.1.48.lock"), "utf8"), "owner");
});


test("buildLaunchSpec forwards arguments and pins the npm-owned CodeGraph", () => {
  const packageRoot = path.resolve("C:\\npm\\node_modules\\project-kb-cli");
  const spec = buildLaunchSpec({
    pythonCommand: "C:\\runtime\\Scripts\\python.exe",
    packageRoot,
    args: ["init", "--json"],
    env: { PATH: "test" },
    platform: "win32",
  });

  assert.equal(spec.command, "C:\\runtime\\Scripts\\python.exe");
  assert.deepEqual(spec.args, ["-m", "project_knowledge", "init", "--json"]);
  assert.equal(
    spec.env.CODEGRAPH_COMMAND,
    path.join(packageRoot, "node_modules", ".bin", "codegraph.cmd"),
  );
  assert.equal(spec.env.PATH, "test");
  assert.equal(spec.env.PYTHONUTF8, "1");
  assert.equal(spec.env.PYTHONIOENCODING, "utf-8");
});


test("buildLaunchSpec preserves an explicit CodeGraph override", () => {
  const spec = buildLaunchSpec({
    pythonCommand: "python",
    packageRoot: "C:\\package",
    args: [],
    env: { CODEGRAPH_COMMAND: "D:\\custom\\codegraph.cmd" },
    platform: "win32",
  });
  assert.equal(spec.env.CODEGRAPH_COMMAND, "D:\\custom\\codegraph.cmd");
});
