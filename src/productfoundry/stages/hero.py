"""hero stage — generate the cover hero artwork.

Produces a SINGLE cover-quality image (English copy) shared by every
language output: the artwork is generated once by the image model with the
exact localized (English) title, subtitle, series, age badge and author
embedded inside a clearly separated text zone. The vision judge verifies the
copy letter by letter (including accents) and the stage retries with
corrections until the copy is exact or attempts are exhausted.

The same image is reused for every language package: the cover is not
re-generated per language (cost/benefit: one spectacular cover, not two).
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from productfoundry.domain.assets import AssetPlan, AssetSpec
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.packaging import localized_age_label
from productfoundry.providers import ImageGenerationRequest
from productfoundry.providers.pricing import image_cost_usd
from productfoundry.series import canonical_character_reference
from productfoundry.stages.audit import _audit_single_image, _is_audit_enabled
from productfoundry.stages.story_helpers import localized_series_name

PROMPT_VERSION = "hero-v4"


def _get_author(pack) -> str:
    profile = getattr(pack, "profile", None)
    if profile is None:
        return ""
    return getattr(profile, "author", "") or ""


def _official_palette(pack) -> str:
    """Official colors of the main character (canonical palette, en)."""
    palettes = getattr(pack, "palettes", None) or {}
    stories = (getattr(pack, "stories", None) or {})
    roster = stories.get("characters", []) if isinstance(stories, dict) else []
    for character in roster:
        if isinstance(character, dict) and character.get("role") == "main":
            return (palettes.get(character.get("id", ""), {}).get("en") or "").strip()
    return ""


def _build_hero_prompt(plan: ProductPlan, pack, language: str = "en", story_id: str = "") -> str:
    """Prompt for the single shared cover artwork (English copy embedded)."""
    title = plan.titles.get(language) or plan.titles.get("en") or plan.theme.capitalize()
    age_badge = localized_age_label(language, getattr(pack.profile, "age_range", "") or "")
    author = _get_author(pack)
    series = localized_series_name(pack, language)
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
        "Star quality composition."
    )
    parts.append(scene)

    palette = _official_palette(pack)
    if palette:
        parts.append(f"The protagonist's official colors MUST be: {palette}. Keep them exactly.")

    copy_lines = [title]
    if series:
        copy_lines.append(series)
    if age_badge:
        copy_lines.append(age_badge)
    if author:
        copy_lines.append(author)
    text_block = "\n".join(copy_lines)

    parts.append(
        "The cover includes a clearly separated text zone: a large decorative sign, "
        "banner, cloud, arch or frame with a plain surface reserved for text. "
        "Render the following text inside that zone ONLY, exactly as written, "
        "with correct spelling, accents, and punctuation, in a clean legible "
        f"display font, with each line on its own row:\n{text_block}\n"
        "Do not add any other words, letters, watermark, or signature anywhere "
        "else in the image."
    )
    parts.append("vibrant saturated colors, soft pastel palette, dreamy atmosphere")
    parts.append("high quality, illustration, large format poster art")
    if negative:
        cleaned_negative = (
            negative.replace("color", "")
            .replace("black and white", "")
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
    inputs: ClassVar = ["concept"]
    outputs: ClassVar = ["hero"]
    input_models: ClassVar = {"concept": ProductPlan}
    prompt_version = PROMPT_VERSION
    provider_key = "image"
    max_attempts = 3

    def output_files(self, ctx: StageContext) -> list[Path]:
        # ONE shared artwork for every language output (English copy embedded).
        return [ctx.assets_dir / "cover_hero.png"]

    def expected_output_files(self, ctx: StageContext) -> list[Path] | None:
        return self.output_files(ctx)

    def extra_hash_inputs(self, ctx: StageContext) -> list[str]:
        import hashlib

        hashes: list[str] = []
        stories = getattr(ctx.pack, "stories", None) or {}
        roster = stories.get("characters", []) if isinstance(stories, dict) else []
        for character in roster:
            char_id = character.get("id") if isinstance(character, dict) else None
            if not char_id:
                continue
            try:
                reference = canonical_character_reference(ctx.pack, char_id)
            except ValueError:
                continue
            if reference.exists():
                hashes.append(hashlib.sha256(reference.read_bytes()).hexdigest()[:16])
        legacy = ctx.assets_dir / "character_sheet.png"
        if legacy.exists():
            hashes.append(hashlib.sha256(legacy.read_bytes()).hexdigest()[:16])
        return hashes

    def _expected_copy(self, ctx: StageContext, concept: ProductPlan) -> dict[str, str]:
        language = "en"
        age_badge = localized_age_label(language, getattr(ctx.pack.profile, "age_range", "") or "")
        return {
            "expected_title": concept.titles.get(language, concept.titles.get("en", ctx.request.theme)),
            "expected_subtitle": localized_series_name(ctx.pack, language),
            "expected_age_badge": age_badge,
            "expected_author": _get_author(ctx.pack),
        }

    def _reference_images(self, ctx: StageContext) -> list[bytes]:
        """Canonical main-character PNG; falls back to a legacy sheet."""
        refs: list[bytes] = []
        stories = getattr(ctx.pack, "stories", None) or {}
        roster = stories.get("characters", []) if isinstance(stories, dict) else []
        for character in roster:
            if isinstance(character, dict) and character.get("role") == "main":
                try:
                    refs.append(canonical_character_reference(ctx.pack, character.get("id", "")).read_bytes())
                except (ValueError, OSError):
                    pass
                break
        if not refs:
            legacy = ctx.assets_dir / "character_sheet.png"
            if legacy.exists():
                refs.append(legacy.read_bytes())
        return refs

    def run(self, ctx: StageContext, concept: ProductPlan) -> AssetPlan:
        gen_size = ctx.pack.profile.image_size
        refs = self._reference_images(ctx)

        hero_path = ctx.assets_dir / "cover_hero.png"
        prompt = _build_hero_prompt(concept, ctx.pack, "en", ctx.request.story_id)
        if not hero_path.exists():
            generate_hero(
                prompt, ctx.image_provider, gen_size, hero_path,
                reference_images=refs,
                on_cost=lambda size, quality: ctx.set_cost(
                    image_cost_usd(ctx.runtime.image.provider, ctx.runtime.image.model, size, quality)
                ),
            )
        expected = self._expected_copy(ctx, concept)
        spec = AssetSpec(
            id="cover_hero", page_id="cover_hero", prompt=prompt,
            aspect_ratio="1:1", size=gen_size, quality="high",
            expected_title=expected["expected_title"],
            expected_subtitle=expected["expected_subtitle"],
            expected_age_badge=expected["expected_age_badge"],
            expected_author=expected["expected_author"],
        )
        if not _is_audit_enabled(ctx.pack):
            spec.audit_status = "ok"
            spec.audit_notes = "audit disabled"
            return AssetPlan(assets=[spec])
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
                f"cover hero failed the judge after {attempt} attempt(s): {verdict.notes}"
            )
        spec.audit_status = verdict.status
        spec.audit_notes = verdict.notes
        return AssetPlan(assets=[spec])