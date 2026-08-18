from __future__ import annotations

import tempfile
from pathlib import Path

from project_knowledge.config import ProjectConfig
from project_knowledge.engine import CodeIndexSnapshot, IndexedFile
from project_knowledge.frameworks import FrameworkIndex
from project_knowledge.knowledge import KnowledgeGenerator
from project_knowledge.models import Symbol
from project_knowledge.retrieval import KnowledgeAPI
from project_knowledge.service import ProjectService
from project_knowledge.store import KnowledgeStore


class FakeCodeGraphEngine:
    def __init__(self, symbols: dict[str, list[Symbol]], sources: dict[str, str]) -> None:
        self.symbols = symbols
        self.sources = sources
        self.queries: list[str] = []

    def search_symbols(self, root, config, query, limit=20):
        self.queries.append(query)
        return self.symbols.get(query, [])[:limit]

    def get_source(self, root, path, start_line=None, end_line=None):
        return self.sources[path]

    def snapshot(self, root, config):
        return CodeIndexSnapshot(
            "fixture",
            tuple(IndexedFile(path, "Python", Path(path).stem, 1, 1, "sha256:fixture") for path in self.sources),
        )


def symbol(path: str, name: str, line: int = 1) -> Symbol:
    return Symbol(
        id=f"{path}::{name}", name=name, kind="import", path=path,
        line=line, end_line=line,
    )


def test_fastapi_detector_returns_entrypoint_and_registration_evidence() -> None:
    source = """from fastapi import FastAPI, APIRouter
app = FastAPI()
router = APIRouter()
@router.get('/items')
def list_items():
    return []
app.include_router(router)
"""
    engine = FakeCodeGraphEngine(
        {"FastAPI": [symbol("src/api.py", "FastAPI")]},
        {"src/api.py": source},
    )

    result = FrameworkIndex(engine).detect(Path("."), ProjectConfig(engine="codegraph"))

    detection = result.detections[0]
    assert detection.framework == "fastapi"
    assert detection.confidence >= 0.8
    assert detection.entrypoints[0].path == "src/api.py"
    assert detection.registration_points
    assert detection.evidence[0].symbol_id == "src/api.py::FastAPI"
    assert result.fact_source == "codegraph"


def test_skynet_detector_exposes_lifecycle_and_service_registration() -> None:
    source = """local skynet = require 'skynet'
skynet.start(function()
  skynet.dispatch('lua', function() end)
  skynet.newservice('worker')
end)
"""
    engine = FakeCodeGraphEngine(
        {"skynet.start": [symbol("service/main.lua", "skynet.start")]},
        {"service/main.lua": source},
    )

    result = FrameworkIndex(engine).detect(Path("."), ProjectConfig(engine="codegraph"))

    detection = next(item for item in result.detections if item.framework == "lua-skynet")
    assert detection.entrypoints
    assert detection.registration_points
    assert detection.lifecycle


def test_flask_and_django_profiles_require_their_own_markers() -> None:
    cases = [
        (
            "flask", "Flask", "src/flask_app.py", "Flask",
            "from flask import Flask\napp = Flask(__name__)\n@app.route('/health')\ndef health(): pass\n",
        ),
        (
            "django", "urlpatterns", "src/urls.py", "urlpatterns",
            "from django.urls import path\nurlpatterns = [path('health/', health)]\n",
        ),
    ]
    for framework, query, path, name, source in cases:
        engine = FakeCodeGraphEngine({query: [symbol(path, name)]}, {path: source})

        result = FrameworkIndex(engine).detect(Path("."), ProjectConfig(engine="codegraph"))

        detection = next(item for item in result.detections if item.framework == framework)
        assert detection.entrypoints
        assert all(item.framework == framework for item in detection.evidence)


def test_generic_route_name_does_not_create_framework_fact() -> None:
    engine = FakeCodeGraphEngine(
        {"route": [symbol("src/app.py", "route")]},
        {"src/app.py": "def route():\n    return 1\n"},
    )

    result = FrameworkIndex(engine).detect(Path("."), ProjectConfig(engine="codegraph"))

    assert result.detections == []
    assert result.unknowns
    assert "route" not in engine.queries


def test_test_names_and_profile_constants_do_not_create_framework_fact() -> None:
    engine = FakeCodeGraphEngine(
        {"FastAPI": [symbol("tests/test_fastapi.py", "test_fastapi_detector")]},
        {
            "tests/test_fastapi.py": 'fixture = "from fastapi import FastAPI\\napp = FastAPI()"\n',
            "src/project_knowledge/frameworks.py": 'DEFAULT = ("FastAPI", "APIRouter")\n',
        },
    )

    result = FrameworkIndex(engine).detect(Path("."), ProjectConfig(engine="codegraph"))

    assert result.detections == []
    assert result.unknowns


def test_generator_indexes_framework_page_with_codegraph_sources() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "src").mkdir()
        source_path = root / "src" / "api.py"
        source_path.write_text("from fastapi import FastAPI\napp = FastAPI()\n@app.get('/')\ndef root(): pass\n", encoding="utf-8")
        config = ProjectConfig(project_name="sample", engine="codegraph")
        config.write(root)
        engine = FakeCodeGraphEngine(
            {"FastAPI": [symbol("src/api.py", "FastAPI")]},
            {"src/api.py": source_path.read_text(encoding="utf-8")},
        )
        db_path = root / ".project-kb" / "index.db"
        with KnowledgeStore(db_path) as store:
            store.initialize()
            store.replace_code_snapshot(CodeIndexSnapshot(
                "fixture", (IndexedFile("src/api.py", "Python", "api.py", 1, 1, "sha256:fixture"),)
            ))
            store.set_meta("head_commit", "fixture")
            records = KnowledgeGenerator(root, config, store, engine=engine).generate()

            record = store.get_knowledge("generated.frameworks")

        assert record is not None
        assert record.kind == "framework"
        assert any(item.path == "src/api.py" for item in record.sources)
        assert "fastapi" in record.content
        assert any(item.id == "generated.frameworks" for item in records)
        assert (root / config.generated_root / "frameworks.md").exists()


def test_real_codegraph_fixture_generates_fastapi_framework_index() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "src").mkdir()
        (root / "src" / "api.py").write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "@app.get('/health')\n"
            "def health():\n"
            "    return {'ok': True}\n",
            encoding="utf-8",
        )
        ProjectConfig(project_name="framework-fixture", engine="codegraph").write(root)

        service = ProjectService(root)
        service.initialize()

        with KnowledgeStore(service.db_path, readonly=True) as store:
            record = store.get_knowledge("generated.frameworks")
        assert record is not None
        assert "fastapi" in record.tags
        assert any(source.path == "src/api.py" for source in record.sources)
        assert "@app.get" in record.content
        search = KnowledgeAPI(root).search("fastapi", kinds=["framework"])
        assert search["results"][0]["id"] == "generated.frameworks"
