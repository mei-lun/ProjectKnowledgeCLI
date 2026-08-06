#!/usr/bin/env python3
"""Print machine-readable project-kb health for client integrations."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    project = sys.argv[1] if len(sys.argv) > 1 else "."
    result = subprocess.run(["project-kb", "status", project, "--json"], check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

