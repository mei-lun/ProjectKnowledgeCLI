from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from project_knowledge.util import atomic_write  # noqa: E402
from project_knowledge.versioning import read_project_version  # noqa: E402


def stage_npm_package(root: Path, output: Path, wheel: Path) -> dict[str, str]:
    root = root.resolve()
    output = output.resolve()
    wheel = wheel.resolve()
    version = read_project_version(root)
    template_path = root / "npm" / "package.template.json"
    template: dict[str, Any] = json.loads(template_path.read_text(encoding="utf-8"))
    if "version" in template:
        raise ValueError("npm/package.template.json must not contain version")

    wheel_pattern = re.compile(
        rf"^project_knowledge_cli-{re.escape(version)}-[^-]+-[^-]+-[^.]+\.whl$"
    )
    if not wheel.is_file() or not wheel_pattern.fullmatch(wheel.name):
        raise ValueError(f"expected a project_knowledge_cli wheel for version {version}: {wheel}")
    if output == root or root not in output.parents:
        raise ValueError(f"npm staging output must stay inside the project root: {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for directory in ["bin", "lib", "scripts"]:
            source = root / "npm" / directory
            if not source.is_dir():
                raise ValueError(f"missing npm package directory: {source}")
            shutil.copytree(source, temporary / directory)
        for filename in ["README.md", "LICENSE"]:
            source = root / filename
            if not source.is_file():
                raise ValueError(f"missing npm package file: {source}")
            shutil.copy2(source, temporary / filename)
        vendor = temporary / "vendor"
        vendor.mkdir()
        shutil.copy2(wheel, vendor / wheel.name)
        manifest = dict(template)
        manifest["version"] = version
        atomic_write(
            temporary / "package.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        if output.exists():
            shutil.rmtree(output)
        temporary.replace(output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {"version": version, "output": str(output), "wheel": wheel.name}


def build_python_wheel(root: Path, wheelhouse: Path) -> Path:
    root = root.resolve()
    wheelhouse = wheelhouse.resolve()
    if wheelhouse.exists():
        shutil.rmtree(wheelhouse)
    wheelhouse.mkdir(parents=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(wheelhouse),
            str(root),
        ],
        cwd=root,
        check=True,
    )
    version = read_project_version(root)
    matches = sorted(wheelhouse.glob(f"project_knowledge_cli-{version}-*.whl"))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one built wheel for {version}; found {len(matches)}")
    return matches[0]


def build_npm_package(root: Path, output: Path) -> dict[str, str]:
    wheel = build_python_wheel(root, root / "dist" / "python-wheel")
    return stage_npm_package(root, output, wheel)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the project-kb-cli npm package staging directory")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = (args.output or (root / "dist" / "npm-package")).resolve()
    print(json.dumps(build_npm_package(root, output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
