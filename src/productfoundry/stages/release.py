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
from productfoundry.domain.manifest import PublicationManifest, sha256_file, validate_compliance
from productfoundry.domain.packaging import PackagePlan
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.hashing import sha256_text
from productfoundry.engine.pipeline import Stage, StageContext

PROMPT_VERSION = "release-v3"

APPROVAL_MARKER = ".release_approved"
APPROVAL_REQUEST = "requested"


class ReleaseReport(BaseModel):
    verdict: str = "fail"  # pass | fail
    publishable: bool = False
    human_release_approved: bool = False
    deliverables_fingerprint: str = ""
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
    gate_verdict = "pass"

    def extra_hash_inputs(self, ctx: StageContext) -> list[str]:
        # The human approval marker is an input: approving/revoking must
        # invalidate the release node. Bind the approval to the current
        # deliverable hashes so regenerated content needs fresh approval.
        marker = ctx.project_dir / APPROVAL_MARKER
        marker_content = marker.read_text() if marker.exists() else ""
        # Include the hash of all existing gate artifacts so a change in any
        # upstream gate result invalidates the release node.
        gate_hashes = []
        for gate_name in ("review", "printcheck", "audit_assets", "lineart_check", "audit_prompt", "audit_character_sheet"):
            env = ctx.get_artifact(gate_name)
            if env is not None:
                import json
                gate_hashes.append(json.dumps(env.artifact, sort_keys=True))
        # Include every deliverable's binary SHA-256 so any byte mutation
        # invalidates the cached approval, regardless of the file's size or
        # whether its bytes are valid UTF-8.
        deliverable_hashes = []
        for root in (ctx.packages_dir, ctx.listings_dir):
            if not root.exists():
                continue
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    deliverable_hashes.append(sha256_file(p))
        return [marker_content, "|".join(gate_hashes), "|".join(deliverable_hashes)]

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

        for gate_name in (
            "audit_prompt",
            "pack_validate",
            "audit_character_sheet",
            "audit_assets",
            "lineart_check",
            "printcheck",
            "review",
        ):
            _gate(gate_name)

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

        # Fingerprint of all deliverables; bound to the approval marker so any
        # change in bytes invalidates prior human approval.
        deliverable_hashes: list[str] = []
        for file in manifest.files:
            deliverable_hashes.append(f"{file.path}:{file.sha256}")
        fingerprint = sha256_text("\n".join(sorted(deliverable_hashes)))
        marker = ctx.project_dir / APPROVAL_MARKER
        marker_content = marker.read_text().strip() if marker.exists() else ""
        # Marker format: "<fingerprint>\napproved" — the fingerprint part binds
        # the approval to the current deliverables. A marker containing only
        # "requested" is a fresh human approval request written by
        # `release --approve`: bind it to the fingerprint computed in this run.
        marker_fingerprint = ""
        marker_approved = False
        for line in marker_content.splitlines():
            if line.startswith("fingerprint:"):
                marker_fingerprint = line.split(":", 1)[1].strip()
            elif line == "approved":
                marker_approved = True
        if marker_content == APPROVAL_REQUEST:
            marker_approved = True
            marker_fingerprint = fingerprint
        human_approved = marker_approved and marker_fingerprint == fingerprint
        if marker.exists() and not human_approved:
            marker.unlink()
        manifest.human_release_approved = human_approved
        manifest.deliverables_fingerprint = fingerprint  # type: ignore[attr-defined]

        publishable = manifest.compute_publishable()
        manifest.save(ctx.project_dir)

        if publishable and marker.exists() and human_approved:
            # Persist the binding to the deliverables fingerprint so future
            # content changes invalidate the approval.
            marker.write_text(
                f"fingerprint:{fingerprint}\napproved\n",
                encoding="utf-8",
            )
        elif marker_content == APPROVAL_REQUEST and not publishable:
            # The approval request could not be consumed (a gate failed):
            # remove it so a later run requires a fresh explicit approval.
            marker.unlink()

        return ReleaseReport(
            verdict="pass" if publishable else "fail",
            publishable=publishable,
            human_release_approved=human_approved,
            deliverables_fingerprint=fingerprint,
            manifest=manifest,
        )
