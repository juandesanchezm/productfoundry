"""assets stage — generate one PNG per page using the configured image provider."""
from __future__ import annotations
from pathlib import Path

from pydantic import BaseModel

from productfoundry.domain.assets import AssetPlan, AssetSpec
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.providers import ImageGenerationRequest


PROMPT_VERSION = "assets-v1"


def plan_assets(plan: ProductPlan, image_size: str) -> AssetPlan:
    """Translate a ProductPlan into an AssetPlan (one asset per page)."""
    assets = []
    for page in plan.pages:
        assets.append(
            AssetSpec(
                id=page.id,
                page_id=page.id,
                prompt=page.prompt,
                aspect_ratio="1:1",
                size=image_size,
                quality="high",
            )
        )
    return AssetPlan(assets=assets)


def generate_assets(plan: AssetPlan, image_provider, assets_dir: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    assets_dir.mkdir(parents=True, exist_ok=True)
    for a in plan.assets:
        path = assets_dir / f"{a.id}.png"
        if not path.exists():
            path.write_bytes(
                image_provider.generate(
                    ImageGenerationRequest(
                        prompt=a.prompt,
                        aspect_ratio=a.aspect_ratio,
                        size=a.size,
                        quality=a.quality,
                    )
                )
            )
        out[a.id] = path
    return out


class AssetsStage(Stage):
    stage_name = "assets"
    inputs = ["concept"]
    outputs = ["assets"]
    input_models = {"concept": ProductPlan}
    prompt_version = PROMPT_VERSION
    provider_key = "image"

    def run(self, ctx: StageContext, concept: ProductPlan) -> AssetPlan:
        plan = plan_assets(concept, ctx.pack.profile.image_size)
        generate_assets(plan, ctx.image_provider, ctx.assets_dir)
        return plan
