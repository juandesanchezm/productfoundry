"""release stage — final gate: manifest + human approval.

Collects every deliverable with SHA-256, records every gate verdict, and
computes `publishable`. The product is only publishable when every gate
passed AND a human explicitly approved the release (via
`productfoundry release <product_id> --approve`, which writes the approval
marker and re-runs this stage).
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from productfoundry.domain.assets import AssetPlan
from productfoundry.domain.listing import ListingSet
from productfoundry.domain.manifest import PublicationManifest, validate_compliance
from productfoundry.domain.packaging import PackagePlan
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.pipeline import Stage, StageContext

PROMPT_VERSION = "release-v2"

APPROVAL_MARKER = ".release_approved"


class ReleaseReport(BaseModel):
    verdict: str = "fail"  # pass | fail
    publishable: bool = False
    human_release_approved: bool = False
    manifest: PublicationManifest | None = None


class ReleaseStage(Stage):
    stage_name = "release"
    inputs: ClassVar = ["concept", "assets", "packages", "listings", "review", "printcheck"]
    outputs: ClassVar = ["release"]
    input_models: ClassVar = {
        "concept": ProductPlan,
        "assets": AssetPlan,
        "packages": PackagePlan,
        "listings": ListingSet,
    }
    prompt_version = PROMPT_VERSION

    def extra_hash_inputs(self, ctx: StageContext) -> list[str]:
        # The human approval marker is an input: approving/revoking must
        # invalidate the release node. Bind the approval to the current
        # deliverable hashes so regenerated content needs fresh approval.
        marker = ctx.project_dir / APPROVAL_MARKER
        marker_content = marker.read_text() if marker.exists() else ""
        # Include the hash of all existing gate artifacts so a change in any
        # upstream gate result invalidates the release node.
        gate_hashes = []
        for gate_name in ("review", "printcheck", "audit_assets", "lineart_check"):
            env = ctx.get_artifact(gate_name)
            if env is not None:
                import json
                gate_hashes.append(json.dumps(env.artifact, sort_keys=True))
        return [marker_content, "|".join(gate_hashes)]

    def expected_output_files(self, ctx: StageContext) -> list[Path] | None:
        return [ctx.project_dir / "publication-manifest.json"]

    def run(self, ctx: StageContext, **inputs: BaseModel) -> ReleaseReport:
        manifest = PublicationManifest(
            product_id=ctx.product_id,
            pack_id=ctx.pack.profile.id,
            pack_version=ctx.pack.profile.pack_version,
            created_at=datetime.now(UTC).isoformat(),
        )

        # Deliverables with SHA-256
        for p in sorted(ctx.packages_dir.rglob("*")):
            if p.is_file():
                manifest.add_file(p, ctx.project_dir)
        for p in sorted(ctx.listings_dir.rglob("*.json")):
            if p.is_file():
                manifest.add_file(p, ctx.project_dir)

        # Gate records (fail-closed: missing artifact = fail)
        def _gate(name: str) -> None:
            env = ctx.get_artifact(name)
            verdict = (env.artifact.get("verdict") if env else None) or "fail"
            manifest.add_gate(name, verdict)

        _gate("review")
        _gate("printcheck")
        _gate("audit_assets")
        _gate("lineart_check")

        compliance_errors = validate_compliance(
            ctx.pack.profile.author,
            getattr(ctx.pack, "compliance", {}) or {},
        )
        manifest.author = ctx.pack.profile.author
        manifest.ai_disclosure_ready = not compliance_errors and bool(
            (getattr(ctx.pack, "compliance", {}) or {}).get("compliance", {}).get("kdp_ai_disclosure")
        )
        manifest.compliance_ready = not compliance_errors
        manifest.add_gate(
            "compliance",
            "pass" if manifest.compliance_ready else "fail",
            "; ".join(compliance_errors),
        )
        manifest.synthetic = (
            ctx.runtime.image.provider == "placeholder"
            or ctx.runtime.llm.provider == "placeholder"
        )

        # Human approval: a marker file written by `productfoundry release --approve`.
        human_approved = (ctx.project_dir / APPROVAL_MARKER).exists()
        manifest.human_release_approved = human_approved

        publishable = manifest.compute_publishable()
        manifest.save(ctx.project_dir)

        return ReleaseReport(
            verdict="pass" if publishable else "fail",
            publishable=publishable,
            human_release_approved=human_approved,
            manifest=manifest,
        )
