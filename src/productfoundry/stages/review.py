"""review stage — deterministic quality gate."""
from __future__ import annotations
from pathlib import Path

from pydantic import BaseModel

from productfoundry.domain.assets import AssetPlan
from productfoundry.domain.listing import ListingSet
from productfoundry.domain.packaging import PackagePlan
from productfoundry.domain.product import ProductPlan
from productfoundry.domain.review import ReviewIssue, ReviewReport
from productfoundry.engine.pipeline import Stage, StageContext


PROMPT_VERSION = "review-v1"


def _check_assets(plan: AssetPlan, processed_dir: Path) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    for a in plan.assets:
        path = processed_dir / f"{a.id}.png"
        if not path.exists():
            issues.append(
                ReviewIssue(
                    criterion="asset_existence",
                    severity="error",
                    detail=f"missing processed asset: {path}",
                )
            )
            continue
        if path.stat().st_size < 1000:
            issues.append(
                ReviewIssue(
                    criterion="asset_size",
                    severity="warning",
                    detail=f"asset suspiciously small (<1KB): {path}",
                )
            )
    return issues


def _check_packages(plans: PackagePlan) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    if not plans.packages:
        issues.append(
            ReviewIssue(criterion="packages", severity="error", detail="no packages produced")
        )
    return issues


def _check_listings(listings: ListingSet) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    for l in listings.listings:
        if not l.title:
            issues.append(
                ReviewIssue(
                    criterion="listing_title",
                    severity="error",
                    detail=f"missing title for {l.marketplace}/{l.language}",
                )
            )
        if len(l.tags) < 5:
            issues.append(
                ReviewIssue(
                    criterion="listing_tags",
                    severity="warning",
                    detail=f"few tags for {l.marketplace}/{l.language}: {len(l.tags)}",
                )
            )
    return issues


class ReviewStage(Stage):
    stage_name = "review"
    inputs = ["concept", "assets", "packages", "listings"]
    outputs = ["review"]
    input_models = {
        "concept": ProductPlan,
        "assets": AssetPlan,
        "packages": PackagePlan,
        "listings": ListingSet,
    }
    prompt_version = PROMPT_VERSION

    def run(
        self,
        ctx: StageContext,
        concept: ProductPlan,
        assets: AssetPlan,
        packages: PackagePlan,
        listings: ListingSet,
    ) -> ReviewReport:
        issues: list[ReviewIssue] = []
        issues.extend(_check_assets(assets, ctx.processed_dir))
        issues.extend(_check_packages(packages))
        issues.extend(_check_listings(listings))

        verdict = "fail" if any(i.severity == "error" for i in issues) else "pass"
        scores: dict[str, float] = {
            "concept_pages": float(len(concept.pages)),
            "assets_generated": float(len(assets.assets)),
            "packages_built": float(len(packages.packages)),
            "listings_count": float(len(listings.listings)),
        }
        return ReviewReport(verdict=verdict, issues=issues, scores=scores)
