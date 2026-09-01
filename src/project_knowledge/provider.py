from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .evidence import SecretScanner
from .models import EvidencePack
from .observability import audit_span
from .schemas import EVIDENCE_PACK_SCHEMA, validate_instance
from .util import atomic_json, hash_text, utc_now


CLOUD_AUTHORIZATION = "I_AUTHORIZE_REDACTED_SOURCE_CODE_TRANSFER"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


class ProviderError(RuntimeError):
    pass


class ProviderDisabledError(ProviderError):
    pass


class AuthorizationError(ProviderError):
    pass


class ProviderCancelledError(ProviderError):
    pass


@dataclass(slots=True)
class ProviderConfig:
    provider_id: str = "disabled"
    model_id: str = ""
    endpoint: str = ""
    enabled: bool = False
    allow_network: bool = False
    local_only: bool = True
    authorization: str = ""
    api_key_env: str = ""
    timeout_seconds: int = 30
    max_retries: int = 2
    cache_enabled: bool = True
    checkpoint_enabled: bool = True
    max_files: int = 20
    max_tokens: int = 12_000
    prompt_version: str = "feature-guide-v1"
    output_schema_version: str = "semantic-draft-v1"

    @classmethod
    def from_project_config(cls, config: Any) -> "ProviderConfig":
        return cls(
            provider_id=config.provider_id,
            model_id=config.provider_model,
            endpoint=config.provider_endpoint,
            enabled=config.provider_enabled,
            allow_network=config.provider_allow_network,
            local_only=config.local_only,
            authorization=config.provider_authorization,
            api_key_env=config.provider_api_key_env,
            timeout_seconds=config.provider_timeout_seconds,
            max_retries=config.provider_max_retries,
            cache_enabled=config.provider_cache,
            checkpoint_enabled=config.provider_checkpoint,
            max_files=config.provider_max_files,
            max_tokens=config.provider_max_tokens,
            prompt_version=config.provider_prompt_version,
            output_schema_version=config.provider_output_schema_version,
        )


@dataclass(slots=True)
class ProviderCapabilities:
    structured_output: bool = True
    network: bool = False
    local: bool = True
    retries: bool = False
    cache: bool = True
    checkpoint: bool = True

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(slots=True)
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(slots=True)
class ProviderOutput:
    output: dict[str, Any]
    usage: ProviderUsage = field(default_factory=ProviderUsage)


@dataclass(slots=True)
class GenerationResult:
    output: dict[str, Any]
    provider_id: str
    model_id: str
    prompt_version: str
    output_schema_version: str
    evidence_schema_version: str
    evidence_hash: str
    request_hash: str
    usage: ProviderUsage
    generated_at: str
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["usage"] = self.usage.to_dict()
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, cached: bool) -> "GenerationResult":
        return cls(
            output=dict(payload["output"]),
            provider_id=str(payload["provider_id"]),
            model_id=str(payload["model_id"]),
            prompt_version=str(payload["prompt_version"]),
            output_schema_version=str(payload["output_schema_version"]),
            evidence_schema_version=str(payload["evidence_schema_version"]),
            evidence_hash=str(payload["evidence_hash"]),
            request_hash=str(payload["request_hash"]),
            usage=ProviderUsage(**payload.get("usage", {})),
            generated_at=str(payload["generated_at"]),
            cached=cached,
        )


class ModelProvider(ABC):
    def __init__(self, config: ProviderConfig, capabilities: ProviderCapabilities) -> None:
        self.config = config
        self.provider_id = config.provider_id
        self.model_id = config.model_id
        self.capabilities = capabilities

    @abstractmethod
    def generate_structured(
        self, payload: dict[str, Any], cancel_event: threading.Event,
    ) -> ProviderOutput:
        raise NotImplementedError

    def health(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "enabled": self.config.enabled,
            "capabilities": self.capabilities.to_dict(),
        }


class DisabledProvider(ModelProvider):
    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config, ProviderCapabilities(structured_output=False, cache=False, checkpoint=False))

    def generate_structured(
        self, payload: dict[str, Any], cancel_event: threading.Event,
    ) -> ProviderOutput:
        raise ProviderDisabledError("ModelProvider 默认关闭；请显式配置并授权 Provider")


class PreviewProvider(ModelProvider):
    """Non-executable descriptor used to preview blocked or not-yet-enabled providers."""

    def __init__(self, config: ProviderConfig, capabilities: ProviderCapabilities) -> None:
        super().__init__(config, capabilities)

    def generate_structured(
        self, payload: dict[str, Any], cancel_event: threading.Event,
    ) -> ProviderOutput:
        raise ProviderDisabledError("预览 Provider 永远不会执行请求")


class FakeProvider(ModelProvider):
    def __init__(self, config: ProviderConfig, response: dict[str, Any] | None = None) -> None:
        _validate_enabled(config, expected="fake")
        super().__init__(config, ProviderCapabilities())
        self.response = response
        self.calls = 0

    def generate_structured(
        self, payload: dict[str, Any], cancel_event: threading.Event,
    ) -> ProviderOutput:
        if cancel_event.is_set():
            raise ProviderCancelledError("Provider 请求已取消")
        self.calls += 1
        output = self.response or {
            "status": "fake",
            "task": payload.get("task", ""),
            "evidence_hash": payload.get("metadata", {}).get("evidence_hash", ""),
        }
        copied = json.loads(json.dumps(output, ensure_ascii=False))
        return ProviderOutput(copied, ProviderUsage())


Transport = Callable[[str, dict[str, Any], dict[str, str], int], dict[str, Any]]


class HttpJsonProvider(ModelProvider):
    def __init__(self, config: ProviderConfig, transport: Transport | None = None) -> None:
        local = _validate_http(config)
        super().__init__(config, ProviderCapabilities(network=True, local=local, retries=True))
        self.transport = transport or _http_transport

    def generate_structured(
        self, payload: dict[str, Any], cancel_event: threading.Event,
    ) -> ProviderOutput:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.api_key_env:
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.config.api_key_env) is None:
                raise AuthorizationError("api_key_env 不是合法环境变量名")
            api_key = os.environ.get(self.config.api_key_env)
            if not api_key:
                raise AuthorizationError(f"缺少 Provider 凭据环境变量：{self.config.api_key_env}")
            headers["Authorization"] = f"Bearer {api_key}"

        last_error: BaseException | None = None
        for attempt in range(self.config.max_retries + 1):
            if cancel_event.is_set():
                raise ProviderCancelledError("Provider 请求已取消")
            try:
                with audit_span("dependency.provider_attempt", "http-json", {
                    "attempt": attempt + 1,
                    "max_attempts": self.config.max_retries + 1,
                    "endpoint": self.config.endpoint,
                    "payload": payload,
                    "headers": headers,
                    "timeout_seconds": self.config.timeout_seconds,
                }) as span:
                    response = self.transport(
                        self.config.endpoint, payload, headers, self.config.timeout_seconds,
                    )
                    span.set_output(response)
                    output = response.get("output")
                    if not isinstance(output, dict):
                        raise ProviderError("Provider 响应缺少 object 类型的 output")
                    usage = response.get("usage", {})
                    if not isinstance(usage, dict):
                        usage = {}
                    return ProviderOutput(
                        output=dict(output),
                        usage=ProviderUsage(
                            input_tokens=int(usage.get("input_tokens", 0)),
                            output_tokens=int(usage.get("output_tokens", 0)),
                        ),
                    )
            except (TimeoutError, OSError, urllib.error.URLError) as error:
                last_error = error
                if attempt >= self.config.max_retries:
                    break
        raise ProviderError(
            f"Provider 请求在 {self.config.max_retries + 1} 次尝试后失败："
            f"{type(last_error).__name__ if last_error else 'unknown'}"
        )


class ModelRuntime:
    def __init__(
        self,
        root: str | Path,
        provider: ModelProvider,
        config: ProviderConfig,
        scanner: SecretScanner | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.provider = provider
        self.config = config
        self.scanner = scanner or SecretScanner()

    def preview(self, pack: EvidencePack, output_schema: dict[str, Any]) -> dict[str, Any]:
        validate_instance(pack.to_dict(), EVIDENCE_PACK_SCHEMA)
        by_kind: dict[str, int] = {}
        total = 0
        for item in pack.items:
            for redaction in item.redactions:
                by_kind[redaction.kind] = by_kind.get(redaction.kind, 0) + 1
                total += 1
        payload = self._payload(pack, output_schema)
        issues = provider_policy_issues(self.config)
        return {
            "provider": {
                "provider_id": self.provider.provider_id,
                "model_id": self.provider.model_id,
                "enabled": self.config.enabled,
                "capabilities": self.provider.capabilities.to_dict(),
            },
            "request_fields": sorted(payload),
            "files": [item.path for item in pack.items],
            "omitted_files": [item.to_dict() for item in pack.omitted],
            "estimated_tokens": pack.estimated_tokens,
            "evidence_hash": pack.pack_hash,
            "redactions": {"total": total, "by_kind": dict(sorted(by_kind.items()))},
            "network_would_be_used": self.provider.capabilities.network and not issues,
            "execution_allowed": self.config.enabled and self.provider.provider_id != "disabled" and not issues,
            "policy_issues": issues,
        }

    def generate(
        self,
        pack: EvidencePack,
        output_schema: dict[str, Any],
        cancel_event: threading.Event | None = None,
        post_validate: Callable[[dict[str, Any]], None] | None = None,
    ) -> GenerationResult:
        validate_instance(pack.to_dict(), EVIDENCE_PACK_SCHEMA)
        event = cancel_event or threading.Event()
        if event.is_set():
            raise ProviderCancelledError("Provider 请求已取消")
        payload = self._payload(pack, output_schema)
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        request_hash = hash_text(canonical)
        cache_path = self.root / ".project-kb" / "provider-cache" / f"{request_hash[7:]}.json"
        checkpoint_path = self.root / ".project-kb" / "provider-checkpoints" / f"{request_hash[7:]}.json"

        if self.config.cache_enabled and cache_path.exists():
            with audit_span("dependency.provider_cache", "generate", {
                "provider_id": self.provider.provider_id,
                "model_id": self.provider.model_id,
                "request_hash": request_hash,
                "cache_path": cache_path.as_posix(),
            }) as span:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                result = GenerationResult.from_dict(cached, cached=True)
                validate_instance(result.output, output_schema)
                if post_validate is not None:
                    post_validate(result.output)
                span.set_output(result.to_dict())
                return result

        checkpoint = {
            "checkpoint_id": request_hash,
            "status": "started",
            "provider_id": self.provider.provider_id,
            "model_id": self.provider.model_id,
            "evidence_hash": pack.pack_hash,
            "prompt_version": self.config.prompt_version,
            "output_schema_version": self.config.output_schema_version,
        }
        if self.config.checkpoint_enabled:
            atomic_json(checkpoint_path, checkpoint)
        try:
            with audit_span("dependency.provider", "generate", {
                "provider_id": self.provider.provider_id,
                "model_id": self.provider.model_id,
                "prompt_version": self.config.prompt_version,
                "output_schema_version": self.config.output_schema_version,
                "request_hash": request_hash,
                "payload": payload,
            }) as span:
                provider_output = self.provider.generate_structured(payload, event)
                span.set_output({
                    "output": provider_output.output,
                    "usage": provider_output.usage.to_dict(),
                })
            safe_output = self.scanner.redact_value(provider_output.output)
            if not isinstance(safe_output, dict):
                raise ProviderError("Provider 输出脱敏后不是 object")
            validate_instance(safe_output, output_schema)
            if post_validate is not None:
                post_validate(safe_output)
            result = GenerationResult(
                output=safe_output,
                provider_id=self.provider.provider_id,
                model_id=self.provider.model_id,
                prompt_version=self.config.prompt_version,
                output_schema_version=self.config.output_schema_version,
                evidence_schema_version="evidence-pack-v1",
                evidence_hash=pack.pack_hash,
                request_hash=request_hash,
                usage=provider_output.usage,
                generated_at=utc_now(),
            )
            if self.config.cache_enabled:
                atomic_json(cache_path, result.to_dict())
            if self.config.checkpoint_enabled:
                atomic_json(checkpoint_path, {**checkpoint, "status": "completed"})
            return result
        except Exception as error:
            if self.config.checkpoint_enabled:
                atomic_json(checkpoint_path, {
                    **checkpoint,
                    "status": "cancelled" if isinstance(error, ProviderCancelledError) else "failed",
                    "error_type": type(error).__name__,
                })
            raise

    def _payload(self, pack: EvidencePack, output_schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "task": pack.task,
            "evidence": pack.to_dict(),
            "output_schema": output_schema,
            "metadata": {
                "provider_id": self.provider.provider_id,
                "model_id": self.provider.model_id,
                "prompt_version": self.config.prompt_version,
                "output_schema_version": self.config.output_schema_version,
                "evidence_schema_version": "evidence-pack-v1",
                "evidence_hash": pack.pack_hash,
            },
        }


def create_provider(config: ProviderConfig) -> ModelProvider:
    if config.provider_id == "disabled":
        return DisabledProvider(config)
    if config.provider_id == "fake":
        return FakeProvider(config)
    if config.provider_id == "http-json":
        return HttpJsonProvider(config)
    raise ProviderError(f"不支持的 ModelProvider：{config.provider_id}")


def create_preview_provider(config: ProviderConfig) -> ModelProvider:
    if config.provider_id == "disabled":
        return DisabledProvider(config)
    if config.provider_id == "fake":
        return PreviewProvider(config, ProviderCapabilities())
    if config.provider_id == "http-json":
        parsed = urlparse(config.endpoint)
        local = bool(parsed.hostname and parsed.hostname.lower() in LOCAL_HOSTS)
        return PreviewProvider(
            config,
            ProviderCapabilities(network=True, local=local, retries=True),
        )
    return PreviewProvider(config, ProviderCapabilities(structured_output=False, local=False, cache=False))


def provider_policy_issues(config: ProviderConfig) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if config.provider_id == "disabled":
        return issues
    if config.provider_id not in {"fake", "http-json"}:
        return [{"code": "unsupported_provider", "message": f"不支持的 ModelProvider：{config.provider_id}"}]
    if not config.enabled:
        issues.append({"code": "provider_not_enabled", "message": "Provider 未显式启用"})
    if config.timeout_seconds < 1 or config.max_retries < 0:
        issues.append({"code": "invalid_retry_policy", "message": "超时或重试配置无效"})
    if config.provider_id == "http-json":
        if not config.allow_network:
            issues.append({"code": "network_not_authorized", "message": "allow_network 未启用"})
        parsed = urlparse(config.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            issues.append({"code": "invalid_endpoint", "message": "endpoint 必须是无内嵌凭据的 http/https URL"})
        elif parsed.hostname.lower() not in LOCAL_HOSTS:
            if config.local_only:
                issues.append({"code": "local_only_violation", "message": "local_only 禁止非本机 Provider"})
            if parsed.scheme != "https":
                issues.append({"code": "cloud_requires_https", "message": "非本机 Provider 只允许 HTTPS"})
            if config.authorization != CLOUD_AUTHORIZATION:
                issues.append({"code": "cloud_authorization_missing", "message": "缺少精确的源码外发授权短语"})
    return issues


def _validate_enabled(config: ProviderConfig, *, expected: str) -> None:
    if config.provider_id != expected:
        raise ProviderError(f"Provider 类型应为 {expected}，实际为 {config.provider_id}")
    if not config.enabled:
        raise AuthorizationError(f"Provider {expected} 未显式启用")
    if config.timeout_seconds < 1 or config.max_retries < 0:
        raise ValueError("Provider timeout_seconds 必须大于 0，max_retries 不得小于 0")


def _validate_http(config: ProviderConfig) -> bool:
    _validate_enabled(config, expected="http-json")
    if not config.allow_network:
        raise AuthorizationError("HTTP Provider 未设置 allow_network: true")
    parsed = urlparse(config.endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise AuthorizationError("Provider endpoint 必须是无内嵌凭据的 http/https URL")
    local = parsed.hostname.lower() in LOCAL_HOSTS
    if not local:
        if config.local_only:
            raise AuthorizationError("local_only: true 禁止向非本机 Provider 发送证据")
        if parsed.scheme != "https":
            raise AuthorizationError("非本机 Provider 只允许 HTTPS")
        if config.authorization != CLOUD_AUTHORIZATION:
            raise AuthorizationError("云 Provider 缺少精确的源码外发授权短语")
    return local


def _http_transport(
    endpoint: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ProviderError("Provider HTTP 响应必须是 JSON object")
    return result
