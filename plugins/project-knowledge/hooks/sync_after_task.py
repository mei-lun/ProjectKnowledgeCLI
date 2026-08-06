#!/usr/bin/env python3
"""Portable lifecycle-hook target for clients that support post-task commands."""

from __future__ import annotations

import os
import subprocess


def main() -> int:
    project = os.environ.get("PROJECT_KB_ROOT", ".")
    summary = os.environ.get("PROJECT_KB_TASK_SUMMARY", "AI task synchronization")
    result = subprocess.run(
        ["project-kb", "sync", project, "--task-summary", summary, "--quiet"],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
