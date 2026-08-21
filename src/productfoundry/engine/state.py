"""Production state — node-level execution record."""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

StageStatus = Literal["pending", "running", "done", "failed"]


class NodeRecord(BaseModel):
    name: str
    status: StageStatus
    input_hash: str = ""
    output_path: str = ""
    cost: float = 0.0
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


class ProductState(BaseModel):
    product_id: str
    pack_id: str
    pack_version: int
    request: dict
    nodes: dict[str, NodeRecord] = Field(default_factory=dict)

    def save(self, project_dir: Path) -> None:
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "product.json").write_text(json.dumps(self.model_dump(), indent=2))

    @classmethod
    def load(cls, project_dir: Path) -> "ProductState":
        return cls.model_validate_json((project_dir / "product.json").read_text())

    def node(self, name: str) -> NodeRecord:
        return self.nodes[name]

    def total_cost(self) -> float:
        return sum(n.cost for n in self.nodes.values())
