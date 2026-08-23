"""hero stage — generate the cover hero artwork.

Produces a single cover-quality image that becomes the front cover's
artwork. The hero is always high quality (it's the product's face) and uses
the pack's derived generation size. The hero is audited with a cover-specific
template (not the line-art template) since it's a full-color illustration.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from productfoundry.domain.assets import AssetPlan, AssetSpec
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.packaging import embed_cover_title
from productfoundry.providers import ImageGenerationRequest
from productfoundry.providers.pricing import image_cost_usd
from productfoundry.stages.audit import _audit_single_image, _is_audit_enabled

PROMPT_VERSION = "hero-v3"


def _build_hero_prompt(plan: ProductPlan, pack, language: str = "en") -> str:
    title = plan.titles.get(language) or plan.titles.get("en") or plan.theme.capitalize()
    style = pack.style.get("style", {}) if hasattr(pack, "style") else {}
    positive = (style.get("positive_prompt_suffix") or "").strip()
    negative = (style.get("negative_prompt_suffix") or "").strip()

    parts = []
    if positive:
        parts.append(positive.replace("black and white line art", "vibrant illustration"))
    else:
        parts.append("vibrant illustration")

    scene = (
        f"Stunning cover illustration of {title}. "
        "Centered protagonist as the hero of the cover, smiling at the viewer, "
        "bathed in warm magical light, surrounded by soft floating sparkles and gentle background scenery. "
        "Star quality composition, balanced framing with room for the exact title text "
        "to be integrated at the bottom."
    )
    parts.append(scene)
    parts.append("vibrant saturated colors, soft pastel palette, dreamy atmosphere")
    parts.append("high quality, illustration, large format poster art")
    parts.append(
        f'Render the exact title text "{title}" once, clearly and legibly, as part of the cover artwork. '
        "Do not add any other words, letters, watermark, or signature."
    )
    if negative:
        cleaned_negative = (
            negative.replace("color", "")
            .replace("black and white", "")
            .replace("text", "")
            .replace("signature", "")
        )
        parts.append(f"Do NOT include: {cleaned_negative}")
    return ". ".join(parts)


def generate_hero(
    prompt: str,
    image_provider,
    image_size: str,
    out_path: Path,
    on_cost: Callable[[str, str], None] | None = None,
    reference_images: list[bytes] | None = None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists():
        out_path.write_bytes(
            image_provider.generate(
                ImageGenerationRequest(
                    prompt=prompt,
                    aspect_ratio="1:1",
                    size=image_size,
                    quality="high",
                    reference_images=reference_images,
                )
            )
        )
        if on_cost is not None:
            on_cost(image_size, "high")
    return out_path


class HeroStage(Stage):
    stage_name = "hero"
    inputs = ["concept"]
    outputs = ["hero"]
    input_models = {"concept": ProductPlan}
    prompt_version = PROMPT_VERSION
    provider_key = "image"
    max_attempts = 3

    def output_files(self, ctx: StageContext) -> list[Path]:
        return [ctx.assets_dir / f"cover_hero_{lang}.png" for lang in ctx.request.languages]

    def expected_output_files(self, ctx: StageContext) -> list[Path] | None:
        return self.output_files(ctx)

    def extra_hash_inputs(self, ctx: StageContext) -> list[str]:
        import hashlib

        main_sheet = ctx.assets_dir / "character_sheet.png"
        if main_sheet.exists():
            return [hashlib.sha256(main_sheet.read_bytes()).hexdigest()[:16]]
        return []

    def run(self, ctx: StageContext, concept: ProductPlan) -> AssetPlan:
        gen_size = ctx.pack.profile.image_size
        # Load only the main character sheet for the hero
        refs: list[bytes] = []
        main_sheet = ctx.assets_dir / "character_sheet.png"
        if main_sheet.exists():
            refs.append(main_sheet.read_bytes())
        specs: list[AssetSpec] = []
        for language in ctx.request.languages:
            prompt = _build_hero_prompt(concept, ctx.pack, language)
            title = concept.titles.get(language, concept.titles.get("en", ctx.request.theme))
            hero_path = ctx.assets_dir / f"cover_hero_{language}.png"
            if not hero_path.exists():
                generate_hero(
                    prompt, ctx.image_provider, gen_size, hero_path,
                    reference_images=refs,
                    on_cost=lambda size, quality: ctx.set_cost(
                        image_cost_usd(ctx.runtime.image.provider, ctx.runtime.image.model, size, quality)
                    ),
                )
            spec = AssetSpec(
                id=f"cover_hero_{language}", page_id=f"cover_hero_{language}", prompt=prompt,
                aspect_ratio="1:1", size=gen_size, quality="high",
            )
            if not _is_audit_enabled(ctx.pack):
                spec.audit_status = "ok"
                spec.audit_notes = "audit disabled"
                embed_cover_title(hero_path, title, concept.subtitle)
                specs.append(spec)
                continue
            verdict = _audit_single_image(ctx, spec, hero_path, hero_mode=True)
            attempt = 1
            while verdict.status != "ok" and attempt < self.max_attempts:
                suggestion = (verdict.rewrite_suggestion or verdict.notes or "").strip()
                if suggestion:
                    spec.prompt = f"{spec.prompt}. Correction: {suggestion}"
                hero_path.unlink()
                generate_hero(
                    spec.prompt, ctx.image_provider, gen_size, hero_path,
                    reference_images=refs,
                    on_cost=lambda size, quality: ctx.set_cost(
                        image_cost_usd(ctx.runtime.image.provider, ctx.runtime.image.model, size, quality)
                    ),
                )
                verdict = _audit_single_image(ctx, spec, hero_path, hero_mode=True)
                attempt += 1
            if verdict.status != "ok":
                raise RuntimeError(
                    f"cover hero {language} failed the judge after {attempt} attempt(s): {verdict.notes}"
                )
            embed_cover_title(hero_path, title, concept.subtitle)
            spec.audit_status = verdict.status
            spec.audit_notes = verdict.notes
            specs.append(spec)
        return AssetPlan(assets=specs)
