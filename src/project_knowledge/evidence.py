from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable

from .models import EvidenceItem, EvidencePack, OmittedEvidence, SecretRedaction
from .schemas import EVIDENCE_PACK_SCHEMA, validate_instance
from .util import approx_tokens, hash_text, read_text, run_git, trim_to_tokens


DEFAULT_HIGH_RISK_PATHS = [
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "*.pem",
    "**/*.pem",
    "*.key",
    "**/*.key",
    ".ssh/**",
    "**/.ssh/**",
    "credentials/**",
    "**/credentials/**",
]


class EvidencePolicyError(ValueError):
    """Raised before evidence can escape the configured project boundary."""


class SecretScanner:
    _sensitive_key = re.compile(
        r"(?i)^(?:api[_-]?key|secret|password|passwd|token|access[_-]?token|client[_-]?secret)$"
    )
    _private_key = re.compile(
        r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?"
        r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
        re.DOTALL,
    )
    _bearer = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
    _basic = re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/]{4,}={0,2}")
    _known_token = re.compile(
        r"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})(?![A-Za-z0-9])"
    )
    _assignment = re.compile(
        r"(?im)(?P<prefix>\b(?P<key>api[_-]?key|secret|password|passwd|token|"
        r"access[_-]?token|client[_-]?secret)\b\s*[:=]\s*)"
        r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;#]+)"
    )

    def redact(self, text: str) -> tuple[str, list[SecretRedaction]]:
        findings: list[SecretRedaction] = []

        def replace_simple(kind: str, replacement: str):
            def callback(match: re.Match[str]) -> str:
                findings.append(SecretRedaction(kind, match.string.count("\n", 0, match.start()) + 1, replacement))
                return replacement

            return callback

        redacted = self._private_key.sub(
            replace_simple("private_key", "[REDACTED:private_key]"), text,
        )
        redacted = self._bearer.sub(
            replace_simple("bearer_token", "Bearer [REDACTED:bearer_token]"), redacted,
        )
        redacted = self._basic.sub(
            replace_simple(
                "basic_credentials", "Basic [REDACTED:basic_credentials]",
            ),
            redacted,
        )
        def replace_assignment(match: re.Match[str]) -> str:
            kind = re.sub(r"[-_]", "_", match.group("key").lower())
            replacement = f"[REDACTED:{kind}]"
            findings.append(SecretRedaction(
                kind=kind,
                line=match.string.count("\n", 0, match.start()) + 1,
                replacement=replacement,
            ))
            return match.group("prefix") + replacement

        redacted = self._assignment.sub(replace_assignment, redacted)
        redacted = self._known_token.sub(
            replace_simple("known_token", "[REDACTED:known_token]"), redacted,
        )
        findings.sort(key=lambda item: (item.line, item.kind))
        return redacted, findings

    def redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact(value)[0]
        if isinstance(value, list):
            return [self.redact_value(item) for item in value]
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                text_key = str(key)
                if self._sensitive_key.fullmatch(text_key) and item is not None and item != "":
                    kind = re.sub(r"[-_]", "_", text_key.lower())
                    result[text_key] = f"[REDACTED:{kind}]"
                else:
                    result[text_key] = self.redact_value(item)
            return result
        return value


class EvidencePackBuilder:
    def __init__(
        self,
        root: str | Path,
        *,
        max_files: int = 20,
        max_tokens: int = 12_000,
        high_risk_paths: Iterable[str] | None = None,
        scanner: SecretScanner | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        if max_files < 1:
            raise ValueError("EvidencePack max_files 必须大于 0")
        if max_tokens < 1:
            raise ValueError("EvidencePack max_tokens 必须大于 0")
        self.max_files = max_files
        self.max_tokens = max_tokens
        self.high_risk_paths = list(high_risk_paths or DEFAULT_HIGH_RISK_PATHS)
        self.scanner = scanner or SecretScanner()

    def build(self, task: str, paths: Iterable[str], source_commit: str | None = None) -> EvidencePack:
        if not task.strip():
            raise ValueError("EvidencePack task 不能为空")
        normalized = sorted({self._normalize_path(path) for path in paths})
        items: list[EvidenceItem] = []
        omitted: list[OmittedEvidence] = []
        used_tokens = 0

        for relative in normalized:
            if self._high_risk(relative):
                omitted.append(OmittedEvidence(relative, "high_risk_path"))
                continue
            if len(items) >= self.max_files:
                omitted.append(OmittedEvidence(relative, "file_limit"))
                continue
            candidate = self.root / relative
            try:
                content = read_text(candidate)
            except OSError:
                omitted.append(OmittedEvidence(relative, "unreadable"))
                continue
            redacted, findings = self.scanner.redact(content)
            remaining = self.max_tokens - used_tokens
            if remaining <= 0:
                omitted.append(OmittedEvidence(relative, "token_limit"))
                continue
            if approx_tokens(redacted) > remaining:
                redacted = trim_to_tokens(redacted, remaining)
                while redacted and approx_tokens(redacted) > remaining:
                    redacted = redacted[:-1]
            tokens = approx_tokens(redacted) if redacted else 0
            if tokens > remaining:
                omitted.append(OmittedEvidence(relative, "token_limit"))
                continue
            items.append(EvidenceItem(
                kind="file",
                path=relative,
                content=redacted,
                content_hash=hash_text(redacted),
                tokens=tokens,
                redactions=findings,
            ))
            used_tokens += tokens

        commit = source_commit if source_commit is not None else run_git(self.root, "rev-parse", "HEAD")
        pack = EvidencePack(
            task=task.strip(),
            items=items,
            omitted=omitted,
            files_considered=len(normalized),
            files_included=len(items),
            estimated_tokens=used_tokens,
            pack_hash="sha256:" + "0" * 64,
            source_commit=commit,
        )
        hash_payload = pack.to_dict()
        hash_payload.pop("pack_hash")
        canonical = json.dumps(hash_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        pack.pack_hash = hash_text(canonical)
        validate_instance(pack.to_dict(), EVIDENCE_PACK_SCHEMA)
        return pack

    def _normalize_path(self, raw: str) -> str:
        if not raw or Path(raw).is_absolute() or PureWindowsPath(raw).is_absolute():
            raise EvidencePolicyError(f"EvidencePack 只接受项目内相对路径：{raw!r}")
        candidate = (self.root / raw).resolve()
        try:
            relative = candidate.relative_to(self.root).as_posix()
        except ValueError as error:
            raise EvidencePolicyError(f"EvidencePack 路径越过项目边界：{raw!r}") from error
        if not candidate.is_file():
            raise EvidencePolicyError(f"EvidencePack 文件不存在或不是普通文件：{relative}")
        return relative

    def _high_risk(self, relative: str) -> bool:
        return any(
            fnmatch.fnmatch(relative, pattern)
            or Path(relative).match(pattern)
            for pattern in self.high_risk_paths
        )
