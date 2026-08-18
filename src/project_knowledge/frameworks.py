from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .config import ProjectConfig
from .engine import CodeIndexEngine
from .models import SourceReference, Symbol


@dataclass(slots=True, frozen=True)
class FrameworkEvidence:
    framework: str
    kind: str
    path: str
    line: int
    detail: str
    symbol_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    def source(self) -> SourceReference:
        return SourceReference(
            type="symbol" if self.symbol_id else "file",
            path=self.path,
            id=self.symbol_id,
            line=self.line,
        )


@dataclass(slots=True)
class FrameworkDetection:
    framework: str
    confidence: float
    evidence: list[FrameworkEvidence] = field(default_factory=list)
    entrypoints: list[FrameworkEvidence] = field(default_factory=list)
    registration_points: list[FrameworkEvidence] = field(default_factory=list)
    lifecycle: list[FrameworkEvidence] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "framework": self.framework,
            "confidence": round(self.confidence, 4),
            "evidence": [item.to_dict() for item in self.evidence],
            "entrypoints": [item.to_dict() for item in self.entrypoints],
            "registration_points": [item.to_dict() for item in self.registration_points],
            "lifecycle": [item.to_dict() for item in self.lifecycle],
            "unknowns": list(self.unknowns),
        }


@dataclass(slots=True)
class FrameworkIndexResult:
    detections: list[FrameworkDetection]
    unknowns: list[str]
    fact_source: str = "codegraph"

    def to_dict(self) -> dict[str, object]:
        return {
            "fact_source": self.fact_source,
            "detections": [item.to_dict() for item in self.detections],
            "unknowns": list(self.unknowns),
        }


class FrameworkDetector(Protocol):
    framework_id: str

    def detect(
        self,
        engine: CodeIndexEngine,
        root: Path,
        config: ProjectConfig,
        source_index: Mapping[str, str] | None = None,
    ) -> FrameworkDetection | None: ...


@dataclass(slots=True, frozen=True)
class MarkerFrameworkDetector:
    framework_id: str
    anchors: tuple[str, ...]
    entrypoint_terms: tuple[str, ...] = ()
    registration_terms: tuple[str, ...] = ()
    lifecycle_terms: tuple[str, ...] = ()

    def detect(
        self,
        engine: CodeIndexEngine,
        root: Path,
        config: ProjectConfig,
        source_index: Mapping[str, str] | None = None,
    ) -> FrameworkDetection | None:
        symbols = self._anchor_symbols(engine, root, config)
        symbols_by_path = {symbol.path: symbol for symbol in symbols}
        framework_evidence: list[FrameworkEvidence] = []
        entrypoints: list[FrameworkEvidence] = []
        registrations: list[FrameworkEvidence] = []
        lifecycle: list[FrameworkEvidence] = []
        candidates = dict(source_index or {})
        for symbol in symbols:
            if not _is_ignored_path(symbol.path) and symbol.path not in candidates:
                candidates[symbol.path] = engine.get_source(
                    root, symbol.path, start_line=max(1, symbol.line),
                    end_line=max(symbol.line + 120, symbol.end_line or symbol.line),
                )
        for path, source in candidates.items():
            if _is_ignored_path(path):
                continue
            marker = _find_marker(self.framework_id, self.anchors, source)
            if marker is None:
                continue
            line, detail = marker
            symbol = symbols_by_path.get(path)
            framework_evidence.append(FrameworkEvidence(
                self.framework_id, "framework_marker", path, line,
                detail, symbol.id if symbol else None,
            ))
            entrypoints.extend(self._term_evidence(path, source, self.entrypoint_terms, "entrypoint", symbol))
            registrations.extend(self._term_evidence(path, source, self.registration_terms, "registration", symbol))
            lifecycle.extend(self._term_evidence(path, source, self.lifecycle_terms, "lifecycle", symbol))
        if not framework_evidence:
            return None
        entrypoints = _unique_evidence(entrypoints)
        registrations = _unique_evidence(registrations)
        lifecycle = _unique_evidence(lifecycle)
        role_count = len(entrypoints) + len(registrations) + len(lifecycle)
        confidence = min(0.99, 0.65 + min(len(framework_evidence), 2) * 0.1 + min(role_count, 3) * 0.05)
        unknowns = [] if role_count else [
            f"{self.framework_id} marker was found, but no static entrypoint or registration evidence was confirmed."
        ]
        return FrameworkDetection(
            framework=self.framework_id,
            confidence=confidence,
            evidence=_unique_evidence(framework_evidence),
            entrypoints=entrypoints,
            registration_points=registrations,
            lifecycle=lifecycle,
            unknowns=unknowns,
        )

    def _anchor_symbols(
        self,
        engine: CodeIndexEngine,
        root: Path,
        config: ProjectConfig,
    ) -> list[Symbol]:
        values: list[Symbol] = []
        seen: set[tuple[str, str]] = set()
        for anchor in self.anchors:
            for symbol in engine.search_symbols(root, config, anchor, limit=20):
                key = (symbol.id, symbol.path)
                if key not in seen:
                    seen.add(key)
                    values.append(symbol)
        return values

    def _term_evidence(
        self,
        path: str,
        source: str,
        terms: Sequence[str],
        kind: str,
        symbol: Symbol | None = None,
    ) -> list[FrameworkEvidence]:
        values: list[FrameworkEvidence] = []
        lowered_terms = tuple(term.lower() for term in terms)
        for offset, line in enumerate(source.splitlines()):
            lowered = line.lower()
            if not any(term in lowered for term in lowered_terms):
                continue
            values.append(FrameworkEvidence(
                self.framework_id, kind, path, (symbol.line if symbol else 1) + offset,
                line.strip()[:240], symbol.id if symbol else None,
            ))
        return values


DEFAULT_FRAMEWORK_DETECTORS: tuple[FrameworkDetector, ...] = (
    MarkerFrameworkDetector(
        "fastapi", ("FastAPI", "APIRouter"),
        entrypoint_terms=("@app.get", "@app.post", "@router.get", "@router.post", "websocket("),
        registration_terms=("include_router", "APIRouter("),
        lifecycle_terms=("on_event(\"startup", "lifespan="),
    ),
    MarkerFrameworkDetector(
        "flask", ("Flask", "Blueprint"),
        entrypoint_terms=(".route(", ".get(", ".post("),
        registration_terms=("register_blueprint", "Blueprint("),
        lifecycle_terms=("before_request", "after_request", "teardown_"),
    ),
    MarkerFrameworkDetector(
        "django", ("urlpatterns", "django.urls", "ViewSet"),
        entrypoint_terms=("urlpatterns", "path(", "re_path("),
        registration_terms=("router.register", "admin.site.register", "include("),
        lifecycle_terms=("ready(self", "AppConfig"),
    ),
    MarkerFrameworkDetector(
        "lua-skynet", ("skynet.start", "skynet.dispatch", "skynet.newservice"),
        entrypoint_terms=("skynet.start", "skynet.dispatch"),
        registration_terms=("skynet.newservice", "skynet.uniqueservice", "skynet.register"),
        lifecycle_terms=("skynet.start", "skynet.exit"),
    ),
)


class FrameworkIndex:
    def __init__(
        self,
        engine: CodeIndexEngine,
        detectors: Sequence[FrameworkDetector] = DEFAULT_FRAMEWORK_DETECTORS,
    ) -> None:
        self.engine = engine
        self.detectors = tuple(detectors)

    def detect(self, root: Path, config: ProjectConfig) -> FrameworkIndexResult:
        source_index: dict[str, str] = {}
        try:
            snapshot = self.engine.snapshot(root, config)
        except Exception:
            snapshot = None
        if snapshot is not None:
            for item in snapshot.files:
                if _is_ignored_path(item.path):
                    continue
                try:
                    source_index[item.path] = self.engine.get_source(root, item.path, start_line=1, end_line=200000)
                except Exception:
                    continue
        detections = [
            detection for detector in self.detectors
            if (detection := detector.detect(self.engine, root, config, source_index)) is not None
        ]
        detections.sort(key=lambda item: (-item.confidence, item.framework))
        unknowns = [] if detections else [
            "No supported framework profile matched current CodeGraph evidence."
        ]
        return FrameworkIndexResult(detections=detections, unknowns=unknowns)


def _unique_evidence(values: Sequence[FrameworkEvidence]) -> list[FrameworkEvidence]:
    result: list[FrameworkEvidence] = []
    seen: set[tuple[str, str, int, str]] = set()
    for item in values:
        key = (item.kind, item.path, item.line, item.detail)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _is_ignored_path(path: str) -> bool:
    parts = {part.casefold() for part in Path(path).parts}
    name = Path(path).name.casefold()
    return (
        "tests" in parts
        or "spec" in parts
        or "scripts" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name in {"frameworks.py", "framework_detector.py"}
    )


def _find_marker(
    framework: str,
    anchors: Sequence[str],
    source: str,
) -> tuple[int, str] | None:
    patterns = {
        "fastapi": ("from fastapi import", "fastapi(", "apirouter("),
        "flask": ("from flask import", "flask(", "blueprint("),
        "django": ("from django", "urlpatterns", "django.urls"),
        "lua-skynet": ("require 'skynet'", 'require("skynet")', "skynet.start(", "skynet.dispatch("),
    }.get(framework, tuple(anchor.lower() for anchor in anchors))
    lowered_patterns = tuple(pattern.casefold() for pattern in patterns)
    for number, line in enumerate(source.splitlines(), start=1):
        lowered = line.casefold()
        if any(pattern in lowered for pattern in lowered_patterns):
            return number, line.strip()[:240]
    return None
