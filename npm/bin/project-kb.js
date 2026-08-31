#!/usr/bin/env node
"use strict";

const path = require("node:path");
const { launchCli } = require("../lib/runtime");
const { doctorAgents, installAgents, uninstallAgents } = require("../lib/agent-installer");
const packageManifest = require("../package.json");


const packageRoot = path.resolve(__dirname, "..");

function optionValue(args, name) {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
}

function isGlobalAgentCommand(args) {
  if (args[0] !== "install" && args[0] !== "uninstall") return false;
  if (args.includes("--location")) return true;
  const target = optionValue(args, "--target") || optionValue(args, "--client");
  return Boolean(target && target.split(",").some((item) => ["codex", "pi"].includes(item.trim())));
}

function runAgentCommand(args) {
  const action = args[0];
  const target = optionValue(args, "--target") || optionValue(args, "--client") || "codex,pi";
  const location = optionValue(args, "--location") || "global";
  const options = { target, location, env: process.env, platform: process.platform };
  return action === "install" ? installAgents(options) : uninstallAgents(options);
}

const rawArgs = process.argv.slice(2);
if (isGlobalAgentCommand(rawArgs)) {
  try {
    const result = runAgentCommand(rawArgs);
    process.stdout.write(rawArgs.includes("--json")
      ? JSON.stringify(result, null, 2) + "\n"
      : `${rawArgs[0]} complete: ${result.changedPaths?.length ?? result.removedPaths?.length ?? 0} path(s) changed\n`);
    process.exitCode = 0;
  } catch (error) {
    process.stderr.write(`project-kb: ${error.message}\n`);
    process.exitCode = 1;
  }
} else if (rawArgs[0] === "doctor" && rawArgs.includes("--global")) {
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
