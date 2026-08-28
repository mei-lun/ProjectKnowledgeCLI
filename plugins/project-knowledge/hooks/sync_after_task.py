#!/usr/bin/env python3
"""Portable lifecycle-hook target for clients that support post-task commands."""

from __future__ import annotations

import os
import subprocess
import time


def main() -> int:
    project = os.environ.get("PROJECT_KB_ROOT", ".")
    summary = os.environ.get("PROJECT_KB_TASK_SUMMARY", "AI task synchronization")
    result = subprocess.run(
        ["project-kb", "sync", project, "--task-summary", summary, "--quiet"],
        check=False,
    )
    if result.returncode != 0:
        return result.returncode
    task_id = os.environ.get("PROJECT_KB_TASK_ID", f"hook-{int(time.time())}")
    subprocess.run(
        [
            "project-kb", "task-event", "--project", project,
            "--task-id", task_id, "--summary", summary, "--quiet",
        ],
        check=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
