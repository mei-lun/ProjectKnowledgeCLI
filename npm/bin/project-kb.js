#!/usr/bin/env node
"use strict";

const path = require("node:path");
const { launchCli } = require("../lib/runtime");
const packageManifest = require("../package.json");


const packageRoot = path.resolve(__dirname, "..");

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
