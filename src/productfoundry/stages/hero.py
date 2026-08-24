"""hero stage — generate the cover hero artwork.

For every language in the request we generate (or refresh) a localized cover
hero image with the exact title, subtitle, series, age badge and author
embedded by the image model. The vision judge verifies the copy letter by
letter (including accents) and the stage retries with corrections until the
copy is exact or attempts are exhausted.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from productfoundry.domain.assets import AssetPlan, AssetSpec
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.cost_tracking import estimate_image_cost
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.packaging import localized_age_label
from productfoundry.providers import ImageGenerationRequest
from productfoundry.series import canonical_character_reference
from productfoundry.stages.audit import _audit_single_image, _is_audit_enabled
from productfoundry.stages.story_helpers import localized_series_name

PROMPT_VERSION = "hero-v5"


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
    """Prompt for the localized cover hero artwork (copy embedded in the language)."""
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
    on_cost: Callable[[str, str, dict], None] | None = None,
    reference_images: list[bytes] | None = None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists():
        request = ImageGenerationRequest(
            prompt=prompt,
            aspect_ratio="1:1",
            size=image_size,
            quality="high",
            reference_images=reference_images,
        )
        out_path.write_bytes(image_provider.generate(request))
        if on_cost is not None:
            on_cost(image_size, "high", getattr(request, "usage", None) or {})
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
        return [
            ctx.assets_dir / f"cover_hero_{lang}.png"
            for lang in (ctx.request.languages or ["en"])
        ]

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

    def _expected_copy(self, ctx: StageContext, concept: ProductPlan, language: str) -> dict[str, str]:
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

    def _hero_path(self, assets_dir: Path, language: str) -> Path:
        return assets_dir / f"cover_hero_{language}.png"

    def run(self, ctx: StageContext, concept: ProductPlan) -> AssetPlan:
        gen_size = ctx.pack.profile.image_size
        refs = self._reference_images(ctx)
        assets: list[AssetSpec] = []
        for language in (ctx.request.languages or ["en"]):
            hero_path = self._hero_path(ctx.assets_dir, language)
            prompt = _build_hero_prompt(concept, ctx.pack, language, ctx.request.story_id)
            if not hero_path.exists():
                generate_hero(
                    prompt, ctx.image_provider, gen_size, hero_path,
                    reference_images=refs,
                    on_cost=lambda size, quality, usage: ctx.set_cost(
                        estimate_image_cost(ctx.runtime.image.provider, ctx.runtime.image.model, size, quality, usage)
                    ),
                )
            expected = self._expected_copy(ctx, concept, language)
            spec = AssetSpec(
                id=f"cover_hero_{language}",
                page_id=f"cover_hero_{language}",
                prompt=prompt,
                aspect_ratio="1:1",
                size=gen_size,
                quality="high",
                expected_title=expected["expected_title"],
                expected_subtitle=expected["expected_subtitle"],
                expected_age_badge=expected["expected_age_badge"],
                expected_author=expected["expected_author"],
            )
            if not _is_audit_enabled(ctx.pack):
                spec.audit_status = "ok"
                spec.audit_notes = "audit disabled"
                assets.append(spec)
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
                    on_cost=lambda size, quality, usage: ctx.set_cost(
                        estimate_image_cost(ctx.runtime.image.provider, ctx.runtime.image.model, size, quality, usage)
                    ),
                )
                verdict = _audit_single_image(ctx, spec, hero_path, hero_mode=True)
                attempt += 1
            if verdict.status != "ok":
                raise RuntimeError(
                    f"cover hero[{language}] failed the judge after {attempt} attempt(s): {verdict.notes}"
                )
            spec.audit_status = verdict.status
            spec.audit_notes = verdict.notes
            assets.append(spec)
        return AssetPlan(assets=assets)