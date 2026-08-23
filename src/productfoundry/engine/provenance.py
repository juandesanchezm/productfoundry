"""Provenance envelope for artifact traceability."""
from typing import Any

from pydantic import BaseModel, Field


class ArtifactEnvelope(BaseModel):
    schema_version: int = 1
    artifact: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def wrap(cls, artifact: BaseModel, provenance: dict[str, Any]) -> "ArtifactEnvelope":
        return cls(artifact=artifact.model_dump(), provenance=provenance)

    def unwrap(self) -> dict[str, Any]:
        return self.artifact
