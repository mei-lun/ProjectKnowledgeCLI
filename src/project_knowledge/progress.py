from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import TextIO


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """A stable lifecycle event emitted while a project is initialized."""

    stage: str
    label: str
    state: str
    index: int
    total_stages: int
    current: int | None = None
    total: int | None = None
    detail: str = ""
    error: str | None = None


class TerminalProgressRenderer:
    """Render initialization progress without affecting machine-readable output."""

    _SPINNER = "|/-\\"

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        enabled: bool | None = None,
        start_spinner: bool = True,
        refresh_interval: float = 0.2,
    ) -> None:
        self.stream = stream or sys.stderr
        self.enabled = self.stream.isatty() if enabled is None else enabled
        self._start_spinner = start_spinner
        self._refresh_interval = max(0.05, refresh_interval)
        self._lock = threading.RLock()
        self._active: ProgressEvent | None = None
        self._started_at = 0.0
        self._spinner_index = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __call__(self, event: ProgressEvent) -> None:
        if not self.enabled:
            return
        with self._lock:
            if event.state == "started":
                self._stop_spinner_locked()
                self._active = event
                self._started_at = time.monotonic()
                self._spinner_index = 0
                self._render_locked(final=False)
                if self._start_spinner:
                    self._stop.clear()
                    self._thread = threading.Thread(target=self._spin, daemon=True)
                    self._thread.start()
                return
            self._active = event
            self._render_locked(final=event.state in {"completed", "failed"})
            if event.state in {"completed", "failed"}:
                self._stop_spinner_locked()

    def close(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._stop_spinner_locked()
            if self._active is not None and self._active.state not in {"completed", "failed"}:
                self._render_locked(final=True)

    def _spin(self) -> None:
        while not self._stop.wait(self._refresh_interval):
            with self._lock:
                if self._active is None or self._active.state in {"completed", "failed"}:
                    return
                self._spinner_index += 1
                self._render_locked(final=False)

    def _stop_spinner_locked(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.1, self._refresh_interval * 2))

    def _render_locked(self, *, final: bool) -> None:
        event = self._active
        if event is None:
            return
        elapsed = max(0.0, time.monotonic() - self._started_at)
        status = "完成" if event.state == "completed" else "失败" if event.state == "failed" else "进行中"
        if event.state not in {"completed", "failed"}:
            status = self._SPINNER[self._spinner_index % len(self._SPINNER)]
        progress = ""
        if event.current is not None and event.total is not None and event.total >= 0:
            progress = f" {event.current}/{event.total}"
        detail = f"  {event.detail}" if event.detail else ""
        error = f"  {event.error}" if event.error else ""
        text = f"[{event.index}/{event.total_stages}] {event.label:<18} {status} {elapsed:>6.1f}s{progress}{detail}{error}"
        # Clear the previous line before redrawing so shorter details do not leave stale text.
        self.stream.write("\r\x1b[2K" + text + ("\n" if final else ""))
        self.stream.flush()
