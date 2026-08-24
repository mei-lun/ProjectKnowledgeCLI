"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");


const MINIMUM_PYTHON = [3, 11];


function discoverPython(options = {}) {
  const env = options.env || process.env;
  const platform = options.platform || process.platform;
  const spawnSyncImpl = options.spawnSyncImpl || childProcess.spawnSync;
  const candidates = [];
  if (env.PROJECT_KB_PYTHON) {
    candidates.push({ command: env.PROJECT_KB_PYTHON, argsPrefix: [] });
  }
  if (platform === "win32") {
    candidates.push({ command: "py", argsPrefix: ["-3.11"] });
    candidates.push({ command: "python", argsPrefix: [] });
    candidates.push({ command: "python3", argsPrefix: [] });
  } else {
    candidates.push({ command: "python3", argsPrefix: [] });
    candidates.push({ command: "python", argsPrefix: [] });
  }

  const attempted = [];
  for (const candidate of candidates) {
    const probe = spawnSyncImpl(
      candidate.command,
      [
        ...candidate.argsPrefix,
        "-c",
        "import sys; print('.'.join(str(part) for part in sys.version_info[:3]))",
      ],
      { encoding: "utf8", windowsHide: true, env },
    );
    const version = String(probe.stdout || "").trim();
    attempted.push(`${candidate.command} ${candidate.argsPrefix.join(" ")}`.trim());
    if (probe.status === 0 && compatiblePython(version)) {
      return { ...candidate, version };
    }
  }
  throw new Error(
    `Python 3.11 or newer is required. Checked: ${attempted.join(", ")}. ` +
    "Install Python or set PROJECT_KB_PYTHON to its executable.",
  );
}


function compatiblePython(version) {
  const parts = String(version).split(".").map((part) => Number.parseInt(part, 10));
  if (parts.length < 2 || parts.some((part) => !Number.isInteger(part))) return false;
  return parts[0] > MINIMUM_PYTHON[0]
    || (parts[0] === MINIMUM_PYTHON[0] && parts[1] >= MINIMUM_PYTHON[1]);
}


function runtimeBase(options = {}) {
  const env = options.env || process.env;
  const platform = options.platform || process.platform;
  if (env.PROJECT_KB_RUNTIME_HOME) return path.resolve(env.PROJECT_KB_RUNTIME_HOME);
  if (platform === "win32") {
    if (env.LOCALAPPDATA) {
      return path.resolve(env.LOCALAPPDATA, "ProjectKnowledgeCLI", "runtimes");
    }
    return path.resolve(env.USERPROFILE || os.homedir(), ".project-kb", "runtimes");
  }
  return path.resolve(env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"), "project-kb", "runtimes");
}


function runtimePython(runtimeDirectory, platform = process.platform) {
  return platform === "win32"
    ? path.join(runtimeDirectory, "Scripts", "python.exe")
    : path.join(runtimeDirectory, "bin", "python");
}


function findBundledWheel(packageRoot, packageVersion) {
  const vendor = path.join(packageRoot, "vendor");
  const escaped = packageVersion.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`^project_knowledge_cli-${escaped}-.*\\.whl$`);
  const wheels = fs.existsSync(vendor)
    ? fs.readdirSync(vendor).filter((name) => pattern.test(name))
    : [];
  if (wheels.length !== 1) {
    throw new Error(
      `Expected exactly one bundled Python wheel for ${packageVersion}; found ${wheels.length}`,
    );
  }
  return path.join(vendor, wheels[0]);
}


function ensureRuntime(options) {
  const packageRoot = path.resolve(options.packageRoot);
  const packageVersion = options.packageVersion;
  const env = options.env || process.env;
  const platform = options.platform || process.platform;
  const spawnSyncImpl = options.spawnSyncImpl || childProcess.spawnSync;
  const base = runtimeBase({ env, platform });
  const target = path.join(base, packageVersion);
  if (runtimeReady(target, packageVersion, platform)) return runtimePython(target, platform);

  const wheel = findBundledWheel(packageRoot, packageVersion);
  const python = options.python || discoverPython({ env, platform, spawnSyncImpl });
  const lockPath = path.join(base, `${packageVersion}.lock`);
  fs.mkdirSync(base, { recursive: true });
  const lock = acquireLock(lockPath, target, packageVersion, platform, {
    timeoutMs: options.lockTimeoutMs ?? 120000,
  });
  if (lock === null) return runtimePython(target, platform);

  const temporary = path.join(
    base,
    `.${packageVersion}.${process.pid}.${Math.random().toString(16).slice(2)}`,
  );
  try {
    if (runtimeReady(target, packageVersion, platform)) return runtimePython(target, platform);
    fs.rmSync(temporary, { recursive: true, force: true });
    runChecked(
      "create managed Python environment",
      python.command,
      [...python.argsPrefix, "-m", "venv", temporary],
      { env, spawnSyncImpl },
    );
    const managedPython = runtimePython(temporary, platform);
    runChecked(
      "install bundled project-knowledge-cli wheel",
      managedPython,
      ["-m", "pip", "install", "--disable-pip-version-check", "--no-index", "--find-links", path.dirname(wheel), wheel],
      { env, spawnSyncImpl },
    );
    fs.writeFileSync(
      path.join(temporary, ".complete"),
      `${JSON.stringify({ packageVersion, pythonVersion: python.version })}\n`,
      "utf8",
    );
    if (fs.existsSync(target)) fs.rmSync(target, { recursive: true, force: true });
    fs.renameSync(temporary, target);
    return runtimePython(target, platform);
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
    releaseLock(lockPath, lock);
  }
}


function runtimeReady(target, packageVersion, platform) {
  const marker = path.join(target, ".complete");
  const python = runtimePython(target, platform);
  if (!fs.existsSync(marker) || !fs.existsSync(python)) return false;
  try {
    return JSON.parse(fs.readFileSync(marker, "utf8")).packageVersion === packageVersion;
  } catch (_error) {
    return false;
  }
}


function acquireLock(lockPath, target, packageVersion, platform, options) {
  const started = Date.now();
  while (true) {
    try {
      const descriptor = fs.openSync(lockPath, "wx");
      fs.writeFileSync(descriptor, `${process.pid}\n`, "utf8");
      return descriptor;
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      if (runtimeReady(target, packageVersion, platform)) return null;
      if (Date.now() - started >= options.timeoutMs) {
        throw new Error(`another project-kb setup is running (${lockPath})`);
      }
      sleep(200);
    }
  }
}


function releaseLock(lockPath, descriptor) {
  try {
    fs.closeSync(descriptor);
  } finally {
    fs.rmSync(lockPath, { force: true });
  }
}


function sleep(milliseconds) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}


function runChecked(label, command, args, options) {
  const result = options.spawnSyncImpl(command, args, {
    encoding: "utf8",
    windowsHide: true,
    env: options.env,
  });
  if (result.status === 0) return;
  const detail = String(result.stderr || result.error?.message || `exit ${result.status}`).trim();
  throw new Error(`Failed to ${label}: ${detail}`);
}


function buildLaunchSpec(options) {
  const platform = options.platform || process.platform;
  const env = { ...(options.env || process.env) };
  if (!env.PYTHONUTF8) env.PYTHONUTF8 = "1";
  if (!env.PYTHONIOENCODING) env.PYTHONIOENCODING = "utf-8";
  if (!env.CODEGRAPH_COMMAND) {
    env.CODEGRAPH_COMMAND = path.join(
      path.resolve(options.packageRoot),
      "node_modules",
      ".bin",
      platform === "win32" ? "codegraph.cmd" : "codegraph",
    );
  }
  return {
    command: options.pythonCommand,
    args: ["-m", "project_knowledge", ...(options.args || [])],
    env,
  };
}


function launchCli(options) {
  const pythonCommand = ensureRuntime(options);
  const spec = buildLaunchSpec({ ...options, pythonCommand });
  return new Promise((resolve, reject) => {
    const child = childProcess.spawn(spec.command, spec.args, {
      env: spec.env,
      stdio: "inherit",
      windowsHide: true,
    });
    const handlers = new Map();
    for (const signal of ["SIGINT", "SIGTERM"]) {
      const handler = () => child.kill(signal);
      handlers.set(signal, handler);
      process.on(signal, handler);
    }
    const cleanup = () => {
      for (const [signal, handler] of handlers) process.off(signal, handler);
    };
    child.once("error", (error) => {
      cleanup();
      reject(error);
    });
    child.once("exit", (code, signal) => {
      cleanup();
      resolve({ code, signal });
    });
  });
}


module.exports = {
  buildLaunchSpec,
  compatiblePython,
  discoverPython,
  ensureRuntime,
  findBundledWheel,
  launchCli,
  runtimeBase,
  runtimePython,
};
