from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from project_knowledge.cli import main
from project_knowledge.config import ProjectConfig
from project_knowledge.evidence import EvidencePackBuilder, EvidencePolicyError
from project_knowledge.provider import (
    CLOUD_AUTHORIZATION,
    AuthorizationError,
    FakeProvider,
    HttpJsonProvider,
    ModelRuntime,
    ProviderCancelledError,
    ProviderConfig,
    ProviderDisabledError,
    create_provider,
)
from project_knowledge.schemas import EVIDENCE_PACK_SCHEMA, validate_instance
from project_knowledge.schemas import SchemaValidationError


class ProviderTests(unittest.TestCase):
    def _project(self, root: Path) -> None:
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text(
            'API_KEY="sk-test-super-secret"\ndef run(): return 1\n', encoding="utf-8",
        )
        (root / "src" / "b.py").write_text("def second(): return 2\n", encoding="utf-8")
        (root / "src" / "c.py").write_text("def third(): return 3\n", encoding="utf-8")
        (root / ".env").write_text("PASSWORD=do-not-send\n", encoding="utf-8")

    def test_evidence_pack_is_bounded_redacted_schema_valid_and_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._project(root)
            builder = EvidencePackBuilder(root, max_files=2, max_tokens=200)
            first = builder.build("新增功能", ["src/c.py", "src/a.py", "src/b.py", ".env"])
            second = builder.build("新增功能", ["src/b.py", "src/a.py", "src/c.py", ".env"])

            payload = first.to_dict()
            validate_instance(payload, EVIDENCE_PACK_SCHEMA)
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("sk-test-super-secret", serialized)
            self.assertNotIn("do-not-send", serialized)
            self.assertIn("[REDACTED:api_key]", serialized)
            self.assertEqual(first.pack_hash, second.pack_hash)
            self.assertEqual(first.files_included, 2)
            self.assertLessEqual(first.estimated_tokens, 200)
            self.assertTrue(any(item.path == ".env" and item.reason == "high_risk_path" for item in first.omitted))

    def test_evidence_builder_rejects_absolute_and_outside_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._project(root)
            builder = EvidencePackBuilder(root)
            with self.assertRaises(EvidencePolicyError):
                builder.build("任务", [str((root / "src" / "a.py").resolve())])
            with self.assertRaises(EvidencePolicyError):
                builder.build("任务", ["../outside.py"])

    def test_disabled_and_cloud_providers_fail_before_transport(self) -> None:
        disabled = create_provider(ProviderConfig())
        with self.assertRaises(ProviderDisabledError):
            disabled.generate_structured({}, threading.Event())

        with self.assertRaises(AuthorizationError):
            create_provider(ProviderConfig(
                provider_id="http-json",
                model_id="cloud-model",
                endpoint="https://models.example.com/generate",
                enabled=True,
                allow_network=True,
                local_only=True,
            ))
        with self.assertRaises(AuthorizationError):
            create_provider(ProviderConfig(
                provider_id="http-json",
                model_id="cloud-model",
                endpoint="https://models.example.com/generate",
                enabled=True,
                allow_network=True,
                local_only=False,
                authorization="not-authorized",
            ))

    def test_fake_provider_runtime_caches_and_writes_secret_free_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._project(root)
            pack = EvidencePackBuilder(root).build("新增功能", ["src/a.py"])
            config = ProviderConfig(
                provider_id="fake", model_id="fake-v1", enabled=True,
                prompt_version="feature-guide-v1", output_schema_version="draft-v1",
            )
            provider = FakeProvider(config, {"title": "测试草案", "status": "draft"})
            runtime = ModelRuntime(root, provider, config)
            output_schema = {
                "type": "object",
                "required": ["title", "status"],
                "properties": {"title": {"type": "string"}, "status": {"enum": ["draft"]}},
                "additionalProperties": False,
            }

            first = runtime.generate(pack, output_schema)
            second = runtime.generate(pack, output_schema)

            self.assertEqual(first.output, {"title": "测试草案", "status": "draft"})
            self.assertFalse(first.cached)
            self.assertTrue(second.cached)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(first.evidence_hash, pack.pack_hash)
            self.assertEqual(first.prompt_version, "feature-guide-v1")
            written = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (root / ".project-kb").rglob("*.json")
            )
            self.assertNotIn("sk-test-super-secret", written)
            self.assertIn('"status": "completed"', written)

    def test_local_http_provider_retries_and_honors_cancellation(self) -> None:
        calls: list[dict[str, object]] = []

        def transport(endpoint: str, payload: dict[str, object], headers: dict[str, str], timeout: int) -> dict[str, object]:
            calls.append(payload)
            if len(calls) == 1:
                raise TimeoutError("temporary timeout")
            return {"output": {"ok": True}, "usage": {"input_tokens": 12, "output_tokens": 3}}

        config = ProviderConfig(
            provider_id="http-json", model_id="local-model",
            endpoint="http://127.0.0.1:11434/generate", enabled=True,
            allow_network=True, local_only=True, max_retries=1,
        )
        provider = HttpJsonProvider(config, transport=transport)
        result = provider.generate_structured({"task": "测试"}, threading.Event())
        self.assertEqual(result.output, {"ok": True})
        self.assertEqual(result.usage.input_tokens, 12)
        self.assertEqual(len(calls), 2)

        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(ProviderCancelledError):
            provider.generate_structured({"task": "测试"}, cancelled)

    def test_default_http_transport_calls_explicitly_enabled_loopback_provider(self) -> None:
        received: list[dict[str, object]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                length = int(self.headers["Content-Length"])
                received.append(json.loads(self.rfile.read(length)))
                body = json.dumps({"output": {"ok": True}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = ProviderConfig(
                provider_id="http-json", model_id="local-model",
                endpoint=f"http://127.0.0.1:{server.server_port}/generate",
                enabled=True, allow_network=True, local_only=True,
            )
            result = create_provider(config).generate_structured({"task": "本地测试"}, threading.Event())
            self.assertEqual(result.output, {"ok": True})
            self.assertEqual(received, [{"task": "本地测试"}])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_runtime_redacts_sensitive_output_and_rejects_invalid_output_without_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._project(root)
            pack = EvidencePackBuilder(root).build("任务", ["src/a.py"])
            config = ProviderConfig(provider_id="fake", model_id="fake-v1", enabled=True)
            schema = {
                "type": "object",
                "required": ["title", "api_key"],
                "properties": {"title": {"type": "string"}, "api_key": {"type": "string"}},
                "additionalProperties": False,
            }
            safe = ModelRuntime(
                root, FakeProvider(config, {"title": "草案", "api_key": "provider-leaked-secret"}), config,
            ).generate(pack, schema)
            self.assertEqual(safe.output["api_key"], "[REDACTED:api_key]")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._project(root)
            pack = EvidencePackBuilder(root).build("任务", ["src/a.py"])
            config = ProviderConfig(provider_id="fake", model_id="fake-v1", enabled=True)
            runtime = ModelRuntime(root, FakeProvider(config, {"unexpected": True}), config)
            with self.assertRaises(SchemaValidationError):
                runtime.generate(pack, {
                    "type": "object", "required": ["title"],
                    "properties": {"title": {"type": "string"}}, "additionalProperties": False,
                })
            self.assertFalse(any((root / ".project-kb" / "provider-cache").glob("*.json")))
            checkpoint = next((root / ".project-kb" / "provider-checkpoints").glob("*.json"))
            self.assertEqual(json.loads(checkpoint.read_text(encoding="utf-8"))["status"], "failed")

    def test_cloud_provider_requires_exact_authorization_phrase(self) -> None:
        config = ProviderConfig(
            provider_id="http-json", model_id="cloud-model",
            endpoint="https://models.example.com/generate", enabled=True,
            allow_network=True, local_only=False, authorization=CLOUD_AUTHORIZATION,
        )
        provider = create_provider(config)
        self.assertIsInstance(provider, HttpJsonProvider)

    def test_generate_dry_run_lists_relative_files_and_redactions_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._project(root)
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main([
                    "generate", "新增功能", "--project", str(root),
                    "--file", "src/a.py", "--file", ".env", "--dry-run", "--json",
                ])
            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertNotIn("sk-test-super-secret", rendered)
            self.assertNotIn("do-not-send", rendered)
            preview = json.loads(rendered)
            self.assertEqual(preview["files"], ["src/a.py"])
            self.assertEqual(preview["redactions"]["total"], 1)
            self.assertEqual(preview["omitted_files"][0]["path"], ".env")
            self.assertFalse(preview["network_would_be_used"])

    def test_generate_dry_run_reports_cloud_policy_issues_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._project(root)
            ProjectConfig(
                project_name="sample", local_only=True,
                provider_id="http-json", provider_model="cloud-model",
                provider_endpoint="https://models.example.com/generate",
                provider_enabled=True, provider_allow_network=True,
            ).write(root)
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main([
                    "generate", "新增功能", "--project", str(root),
                    "--file", "src/a.py", "--dry-run", "--json",
                ])
            preview = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertFalse(preview["network_would_be_used"])
            self.assertFalse(preview["execution_allowed"])
            self.assertEqual(
                {item["code"] for item in preview["policy_issues"]},
                {"local_only_violation", "cloud_authorization_missing"},
            )


if __name__ == "__main__":
    unittest.main()
