"""publication manifest — the single source of truth for what was produced.

The manifest records every deliverable with its SHA-256, size, provenance
(model, prompt, references), the gates it passed, and the final release
state. `publishable` can only become true through the release gate, which
requires explicit human approval.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, Field


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_compliance(author: str, config: dict) -> list[str]:
    """Return missing publication declarations without claiming legal clearance."""
    nested = config.get("compliance", config) if isinstance(config, dict) else {}
    if not isinstance(nested, dict):
        nested = {}
    errors: list[str] = []
    if not author.strip():
        errors.append("author is required")
    if nested.get("author", "").strip() != author.strip():
        errors.append("compliance author does not match pack author")
    for key in (
        "ai_generated_content",
        "kdp_ai_disclosure",
        "etsy_ai_disclosure",
        "etsy_production_partner_disclosure",
    ):
        if nested.get(key) is not True:
            errors.append(f"missing compliance declaration: {key}")
    if not str(nested.get("gumroad_rights_statement", "")).strip():
        errors.append("missing compliance declaration: gumroad_rights_statement")
    return errors


class ManifestFile(BaseModel):
    path: str
    sha256: str
    size: int
    format: str = ""
    language: str = ""
    marketplace: str = ""


class GateRecord(BaseModel):
    name: str
    status: str = "fail"  # pass | fail
    detail: str = ""


class PublicationManifest(BaseModel):
    product_id: str
    pack_id: str
    pack_version: int
    created_at: str = ""
    files: list[ManifestFile] = Field(default_factory=list)
    gates: list[GateRecord] = Field(default_factory=list)
    deliverables_fingerprint: str = ""
    author: str = ""
    ai_disclosure_ready: bool = False
    compliance_ready: bool = False
    human_release_approved: bool = False
    synthetic: bool = False
    publishable: bool = False

    def add_file(self, path: Path, project_dir: Path, **meta) -> None:
        rel = str(path.relative_to(project_dir))
        self.files.append(
            ManifestFile(
                path=rel,
                sha256=sha256_file(path),
                size=path.stat().st_size,
                **meta,
            )
        )

    def add_gate(self, name: str, status: str, detail: str = "") -> None:
        self.gates.append(GateRecord(name=name, status=status, detail=detail))

    def compute_publishable(self) -> bool:
        """Release gate: publishable requires every gate pass, AI disclosure
        ready, and explicit human approval. No other code path may set it."""
        gates_ok = bool(self.gates) and all(g.status == "pass" for g in self.gates)
        self.publishable = bool(
            gates_ok
            and self.ai_disclosure_ready
            and self.compliance_ready
            and self.human_release_approved
            and not self.synthetic
        )
        return self.publishable

    def save(self, project_dir: Path) -> Path:
        out = project_dir / "publication-manifest.json"
        out.write_text(self.model_dump_json(indent=2))
        return out
