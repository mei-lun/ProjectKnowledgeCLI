from __future__ import annotations


class ProjectKnowledgeError(RuntimeError):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def to_dict(self) -> dict[str, object]:
        return {"error": self.code, **self.details}


class UnsupportedEngineError(ProjectKnowledgeError):
    def __init__(self, configured_engine: str) -> None:
        super().__init__(
            "unsupported_engine",
            f"unsupported index engine: {configured_engine}",
            configured_engine=configured_engine,
            supported_engines=["codegraph"],
            migration="set index.engine to codegraph and initialize CodeGraph for this project",
        )
