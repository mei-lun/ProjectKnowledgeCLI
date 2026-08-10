from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

from .util import atomic_write


SEMVER_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
VERSION_ASSIGNMENT_PATTERN = re.compile(
    r'^__version__\s*=\s*"(?P<version>\d+\.\d+\.\d+)"\s*$',
    re.MULTILINE,
)


def next_patch_version(version: str) -> str:
    """Return the next patch version while preserving major and minor values."""
    match = SEMVER_PATTERN.fullmatch(version)
    if not match:
        raise ValueError(f"版本必须采用 major.minor.patch 格式：{version}")
    major, minor, patch = (int(value) for value in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def read_project_version(root: Path) -> str:
    version_path = root / "src" / "project_knowledge" / "__init__.py"
    text = version_path.read_text(encoding="utf-8")
    match = VERSION_ASSIGNMENT_PATTERN.search(text)
    if not match:
        raise ValueError(f"未在 {version_path} 中找到 __version__")
    return match.group("version")


def _replace_version(text: str, new_version: str) -> str:
    if not SEMVER_PATTERN.fullmatch(new_version):
        raise ValueError(f"无效的新版本号：{new_version}")
    updated, count = VERSION_ASSIGNMENT_PATTERN.subn(f'__version__ = "{new_version}"', text, count=1)
    if count != 1:
        raise ValueError("版本文件必须且只能包含一个 __version__ 定义")
    return updated


def _prepend_changelog_entry(text: str, version: str, message: str, changed_on: date) -> str:
    if f"## [{version}]" in text:
        raise ValueError(f"变更日志中已经存在版本 {version}")
    entry = f"## [{version}] - {changed_on.isoformat()}\n\n- {message.strip()}\n"
    marker = "\n## ["
    if marker not in text:
        return f"{text.rstrip()}\n\n{entry}"
    introduction, remainder = text.split(marker, 1)
    return f"{introduction.rstrip()}\n\n{entry}\n## [{remainder}"


def bump_patch_version(
    root: Path,
    message: str,
    *,
    dry_run: bool = False,
    changed_on: date | None = None,
) -> tuple[str, str]:
    """Increment the project patch version and prepend a changelog entry."""
    if not message.strip():
        raise ValueError("版本变更说明不能为空")
    root = root.resolve()
    version_path = root / "src" / "project_knowledge" / "__init__.py"
    changelog_path = root / "CHANGELOG.md"
    old_version = read_project_version(root)
    new_version = next_patch_version(old_version)
    version_text = _replace_version(version_path.read_text(encoding="utf-8"), new_version)
    changelog_text = changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else "# 变更日志\n"
    changelog_text = _prepend_changelog_entry(changelog_text, new_version, message, changed_on or date.today())
    plugin_path = root / "plugins" / "project-knowledge" / ".codex-plugin" / "plugin.json"
    plugin_text: str | None = None
    if plugin_path.exists():
        plugin_manifest = json.loads(plugin_path.read_text(encoding="utf-8"))
        if not isinstance(plugin_manifest, dict) or "version" not in plugin_manifest:
            raise ValueError(f"插件清单缺少 version：{plugin_path}")
        plugin_manifest["version"] = new_version
        plugin_text = json.dumps(plugin_manifest, ensure_ascii=False, indent=2) + "\n"
    if not dry_run:
        atomic_write(version_path, version_text)
        atomic_write(changelog_path, changelog_text)
        if plugin_text is not None:
            atomic_write(plugin_path, plugin_text)
    return old_version, new_version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="递增 Project Knowledge CLI 的补丁版本号")
    parser.add_argument("message", help="写入变更日志的中文变更说明")
    parser.add_argument("--project", default=".", help="项目根目录，默认为当前目录")
    parser.add_argument("--dry-run", action="store_true", help="仅显示新版本号，不修改文件")
    args = parser.parse_args(argv)
    old_version, new_version = bump_patch_version(
        Path(args.project),
        args.message,
        dry_run=args.dry_run,
    )
    suffix = "（预览，未写入）" if args.dry_run else ""
    print(f"版本已从 {old_version} 递增到 {new_version}{suffix}")
    return 0
