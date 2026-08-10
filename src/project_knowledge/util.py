from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def hash_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def atomic_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    kwargs: dict[str, Any] = {} if isinstance(content, bytes) else {"encoding": "utf-8", "newline": "\n"}
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, mode, **kwargs) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def run_git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def git_root(path: Path) -> Path:
    resolved = path.resolve()
    found = run_git(resolved, "rev-parse", "--show-toplevel")
    return Path(found).resolve() if found else resolved


def git_status(root: Path) -> dict[str, str | bool | None]:
    output = run_git(root, "status", "--porcelain=v2", "--branch")
    if output is None:
        return {"branch": None, "head_commit": None, "dirty": False}
    branch: str | None = None
    head_commit: str | None = None
    dirty = False
    for line in output.splitlines():
        if line.startswith("# branch.oid "):
            head_commit = line.removeprefix("# branch.oid ")
        elif line.startswith("# branch.head "):
            branch = line.removeprefix("# branch.head ")
        elif line and not line.startswith("#"):
            dirty = True
    return {"branch": branch, "head_commit": head_commit, "dirty": dirty}


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-.")
    return normalized or "root"


def approx_tokens(text: str) -> int:
    ascii_count = sum(1 for char in text if ord(char) < 128)
    return max(1, (ascii_count + 3) // 4 + len(text) - ascii_count)


def trim_to_tokens(text: str, budget: int) -> str:
    if approx_tokens(text) <= budget:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if approx_tokens(text[:mid]) <= max(1, budget - 8):
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + "\n[truncated to token budget]"


class ProjectLockError(RuntimeError):
    pass




@contextmanager
def watcher_lock(root: Path, stale_after: int = 600) -> Iterator[dict[str, Any]]:
    """Acquire the single per-project watcher coordinator lease."""
    lock_path = root / ".project-kb" / "watcher.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "created_at": time.time(), "root": str(root.resolve())}
    for attempt in range(2):
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            break
        except FileExistsError:
            try:
                current = json.loads(lock_path.read_text(encoding="utf-8"))
                owner_pid = int(current.get("pid", 0))
                created_at = float(current.get("created_at", lock_path.stat().st_mtime))
            except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError, OSError):
                continue
            stale = (not process_alive(owner_pid)) or (time.time() - created_at > stale_after)
            if stale and attempt == 0:
                lock_path.unlink(missing_ok=True)
                continue
            raise ProjectLockError(
                f"another watcher coordinator holds {lock_path} (pid={owner_pid})"
            )
    else:
        raise ProjectLockError(f"unable to acquire watcher coordinator {lock_path}")
    try:
        yield payload
    finally:
        try:
            current = json.loads(lock_path.read_text(encoding="utf-8"))
            if int(current.get("pid", 0)) == os.getpid():
                lock_path.unlink(missing_ok=True)
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError, OSError):
            pass


def script_marker_update(path: Path, marker: str, body: str | None) -> bool:
    """Update a shell-script-owned marker block while preserving user content."""
    start = f"# project-kb:{marker}:start"
    end = f"# project-kb:{marker}:end"
    current = read_text(path) if path.exists() else ""
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
    replacement = "" if body is None else f"{start}\n{body.rstrip()}\n{end}\n"
    if pattern.search(current):
        updated = pattern.sub(replacement, current, count=1)
    elif body is None:
        return False
    else:
        updated = current.rstrip() + ("\n\n" if current.strip() else "") + replacement
    if updated == current:
        return False
    atomic_write(path, updated)
    return True

@contextmanager
def project_lock(root: Path, stale_after: int = 600) -> Iterator[None]:
    lock_path = root / ".project-kb" / "write.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": os.getpid(), "created_at": time.time()})
    for attempt in range(2):
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            break
        except FileExistsError:
            try:
                lock_age = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue
            if lock_age > stale_after and attempt == 0:
                lock_path.unlink(missing_ok=True)
                continue
            raise ProjectLockError(f"another project-kb writer holds {lock_path}")
    try:
        yield
    finally:
        try:
            current = json.loads(lock_path.read_text(encoding="utf-8"))
            if current.get("pid") == os.getpid():
                lock_path.unlink(missing_ok=True)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass


def marker_update(path: Path, marker: str, body: str | None) -> bool:
    start = f"<!-- project-kb:{marker}:start -->"
    end = f"<!-- project-kb:{marker}:end -->"
    current = read_text(path) if path.exists() else ""
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
    replacement = "" if body is None else f"{start}\n{body.rstrip()}\n{end}\n"
    if pattern.search(current):
        updated = pattern.sub(replacement, current, count=1)
    elif body is None:
        return False
    else:
        updated = current.rstrip() + ("\n\n" if current.strip() else "") + replacement
    if updated == current:
        return False
    atomic_write(path, updated)
    return True
