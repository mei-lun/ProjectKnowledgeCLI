"use strict";
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const CODEX_START = "# project-kb:codex-mcp:start";
const CODEX_END = "# project-kb:codex-mcp:end";
const PI_START = "// project-kb:pi-extension:start";
const PI_END = "// project-kb:pi-extension:end";
const TEMPLATE_PATH = path.join(__dirname, "..", "templates", "project-kb.ts");

function resolveAgentPaths(options = {}) {
  const env = options.env || process.env;
  const home = options.homedir || os.homedir();
  const codexHome = path.resolve(env.CODEX_HOME || path.join(home, ".codex"));
  const piAgentDir = path.resolve(env.PI_CODING_AGENT_DIR || path.join(home, ".pi", "agent"));
  return { codexHome, codexConfig: path.join(codexHome, "config.toml"), codexAgents: path.join(codexHome, "AGENTS.md"), piAgentDir, piExtension: path.join(piAgentDir, "extensions", "project-kb.ts") };
}

function buildCodexBlock() {
  return [CODEX_START, "[mcp_servers.project_knowledge]", 'command = "project-kb"', 'args = ["mcp", "--project", "."]', CODEX_END].join("\n");
}

function buildAgentsBlock() {
  return ["<!-- project-kb:instructions:start -->", "Use project-kb knowledge_status before broad exploration and knowledge_context for the task.", "<!-- project-kb:instructions:end -->"].join("\n");
}

function buildPiExtension() {
  const template = fs.readFileSync(TEMPLATE_PATH, "utf8");
  return template.endsWith("\n") ? template : template + "\n";
}

function installAgents(options = {}) {
  assertWindows(options);
  const targets = normalizeTargets(options.target || ["codex", "pi"]);
  if (options.location && options.location !== "global") throw new Error("Windows agent installer currently supports --location=global only");
  const paths = resolveAgentPaths(options);
  const changedPaths = [];
  if (targets.includes("codex")) {
    if (upsertCodex(paths.codexConfig)) changedPaths.push(paths.codexConfig);
    if (upsertMarkdown(paths.codexAgents, buildAgentsBlock(), "<!-- project-kb:instructions:start -->", "<!-- project-kb:instructions:end -->")) changedPaths.push(paths.codexAgents);
  }
  if (targets.includes("pi") && upsertWholeFile(paths.piExtension, buildPiExtension(), PI_START, PI_END)) changedPaths.push(paths.piExtension);
  return { changedPaths, paths, targets };
}

function uninstallAgents(options = {}) {
  assertWindows(options);
  const targets = normalizeTargets(options.target || ["codex", "pi"]);
  const paths = resolveAgentPaths(options);
  const removedPaths = [];
  if (targets.includes("codex")) {
    if (removeCodex(paths.codexConfig)) removedPaths.push(paths.codexConfig);
    if (removeMarked(paths.codexAgents, "<!-- project-kb:instructions:start -->", "<!-- project-kb:instructions:end -->")) removedPaths.push(paths.codexAgents);
  }
  if (targets.includes("pi") && removeWholeFile(paths.piExtension, PI_START, PI_END)) removedPaths.push(paths.piExtension);
  return { removedPaths, paths, targets };
}

function doctorAgents(options = {}) {
  assertWindows(options);
  const env = options.env || process.env;
  const paths = resolveAgentPaths(options);
  const launcher = "project-kb.cmd";
  const pathEntries = String(env.PATH || "").split(path.delimiter).filter(Boolean);
  const npmGlobalBin = env.npm_config_prefix ? path.join(env.npm_config_prefix, "") : null;
  const launcherOnPath = pathEntries.some((entry) => fs.existsSync(path.join(entry, launcher)));
  const runtimeHome = env.PROJECT_KB_RUNTIME_HOME || (env.LOCALAPPDATA ? path.join(env.LOCALAPPDATA, "ProjectKnowledgeCLI", "runtimes") : path.join(os.homedir(), ".project-kb", "runtimes"));
  return {
    platform: process.platform,
    codexHome: paths.codexHome,
    codexConfig: paths.codexConfig,
    codexConfigured: fs.existsSync(paths.codexConfig) && fs.readFileSync(paths.codexConfig, "utf8").includes(CODEX_START),
    piAgentDir: paths.piAgentDir,
    piConfigured: fs.existsSync(paths.piExtension),
    launcher,
    launcherOnPath,
    npmGlobalBin,
    runtimeHome: path.resolve(runtimeHome),
    codegraph: { managedByProjectKb: true, override: env.CODEGRAPH_COMMAND || null },
  };
}

function normalizeTargets(value) {
  const targets = Array.isArray(value) ? value : String(value).split(",");
  const result = [...new Set(targets.map((item) => String(item).trim()).filter(Boolean))];
  const unknown = result.filter((item) => !["codex", "pi"].includes(item));
  if (unknown.length) throw new Error("Unknown agent target(s): " + unknown.join(", ") + ". Known: codex, pi");
  return result;
}

function assertWindows(options) {
  if ((options.platform || process.platform) !== "win32") throw new Error("Windows agent installer is only supported on win32");
}

function upsertCodex(file) {
  const current = read(file);
  const marker = extractMarked(current, CODEX_START, CODEX_END);
  const without = marker ? current.replace(marker, "").trimEnd() : current;
  if (!marker && /\[mcp_servers\.project_knowledge\]/.test(current)) throw new Error("Codex config already defines unowned mcp_servers.project_knowledge: " + file);
  const next = (without ? without + "\n\n" : "") + buildCodexBlock() + "\n";
  if (next === current) return false;
  atomicWrite(file, next);
  return true;
}

function removeCodex(file) {
  if (!fs.existsSync(file)) return false;
  const current = fs.readFileSync(file, "utf8");
  const marker = extractMarked(current, CODEX_START, CODEX_END);
  if (!marker) return false;
  const next = current.replace(marker, "").trim();
  if (next) atomicWrite(file, next + "\n"); else fs.unlinkSync(file);
  return true;
}

function upsertMarkdown(file, body, start, end) {
  const current = read(file);
  const marker = extractMarked(current, start, end);
  const without = marker ? current.replace(marker, "").trimEnd() : current.trimEnd();
  const next = (without ? without + "\n\n" : "") + body + "\n";
  if (next === current) return false;
  atomicWrite(file, next);
  return true;
}

function upsertWholeFile(file, body, start, end) {
  const current = read(file);
  const marker = extractMarked(current, start, end);
  if (marker && current === body) return false;
  if (marker) atomicWrite(file, current.replace(marker, body));
  else if (current.trim()) throw new Error("Pi extension already exists and is not project-kb owned: " + file);
  else atomicWrite(file, body);
  return true;
}

function removeWholeFile(file, start, end) {
  if (!fs.existsSync(file)) return false;
  const current = fs.readFileSync(file, "utf8");
  if (!current.includes(start) || !current.includes(end)) return false;
  fs.unlinkSync(file);
  return true;
}

function removeMarked(file, start, end) {
  if (!fs.existsSync(file)) return false;
  const current = fs.readFileSync(file, "utf8");
  const marker = extractMarked(current, start, end);
  if (!marker) return false;
  const next = current.replace(marker, "").trim();
  if (next) atomicWrite(file, next + "\n"); else fs.unlinkSync(file);
  return true;
}

function extractMarked(content, start, end) {
  const startIndex = content.indexOf(start);
  if (startIndex < 0) return "";
  const endIndex = content.indexOf(end, startIndex + start.length);
  if (endIndex < 0) return "";
  const afterEnd = endIndex + end.length;
  return content.slice(startIndex, content[afterEnd] === "\r" && content[afterEnd + 1] === "\n" ? afterEnd + 2 : content[afterEnd] === "\n" ? afterEnd + 1 : afterEnd);
}

function read(file) { return fs.existsSync(file) ? fs.readFileSync(file, "utf8") : ""; }

function atomicWrite(file, content) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = file + ".tmp." + process.pid;
  fs.writeFileSync(temporary, content, "utf8");
  fs.renameSync(temporary, file);
}

module.exports = { buildAgentsBlock, buildCodexBlock, buildPiExtension, doctorAgents, installAgents, resolveAgentPaths, uninstallAgents };
