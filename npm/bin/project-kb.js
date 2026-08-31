#!/usr/bin/env node
"use strict";

const path = require("node:path");
const { launchCli } = require("../lib/runtime");
const { doctorAgents, installAgents, uninstallAgents } = require("../lib/agent-installer");
let packageManifest;
try {
  packageManifest = require("../package.json");
} catch (_error) {
  packageManifest = require("../../dist/npm-package/package.json");
}


const packageRoot = path.resolve(__dirname, "..");

function optionValue(args, name) {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
}

function isGlobalAgentCommand(args) {
  return args[0] === "agent" && (args[1] === "install" || args[1] === "uninstall") && args.includes("--global");
}

function runAgentCommand(args) {
  const action = args[1];
  const target = optionValue(args, "--target") || optionValue(args, "--client") || "codex,pi";
  const location = "global";
  if (String(target).split(",").some((item) => item.trim() === "codex")) {
    throw new Error("Codex Project Knowledge integration is project-scoped. Run `project-kb init` or `project-kb install --client codex` inside a project.");
  }
  const options = { target, location, env: process.env, platform: process.platform, enforceProjectScope: true };
  return action === "install" ? installAgents(options) : uninstallAgents(options);
}

const rawArgs = process.argv.slice(2);
if (isGlobalAgentCommand(rawArgs)) {
  try {
    const result = runAgentCommand(rawArgs);
    process.stdout.write(rawArgs.includes("--json")
      ? JSON.stringify(result, null, 2) + "\n"
      : `${rawArgs[1]} complete: ${result.changedPaths?.length ?? result.removedPaths?.length ?? 0} path(s) changed\n`);
    process.exitCode = 0;
  } catch (error) {
    process.stderr.write(`project-kb: ${error.message}\n`);
    process.exitCode = 1;
  }
} else if ((rawArgs[0] === "doctor" || (rawArgs[0] === "agent" && rawArgs[1] === "doctor")) && rawArgs.includes("--global")) {
  try {
    const result = doctorAgents({ env: process.env, platform: process.platform });
    process.stdout.write(rawArgs.includes("--json") ? JSON.stringify(result, null, 2) + "\n" : `${JSON.stringify(result, null, 2)}\n`);
    process.exitCode = 0;
  } catch (error) {
    process.stderr.write(`project-kb: ${error.message}\n`);
    process.exitCode = 1;
  }
} else {

launchCli({
  packageRoot,
  packageVersion: packageManifest.version,
  args: process.argv.slice(2),
  env: process.env,
}).then(({ code, signal }) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exitCode = code ?? 1;
}).catch((error) => {
  process.stderr.write(`project-kb: ${error.message}\n`);
  process.exitCode = 1;
});
}
