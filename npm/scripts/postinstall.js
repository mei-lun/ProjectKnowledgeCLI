"use strict";

const path = require("node:path");
const { ensureRuntime } = require("../lib/runtime");
const packageManifest = require("../package.json");


try {
  ensureRuntime({
    packageRoot: path.resolve(__dirname, ".."),
    packageVersion: packageManifest.version,
    env: process.env,
  });
} catch (error) {
  process.stderr.write(`project-kb setup failed: ${error.message}\n`);
  process.exitCode = 1;
}
