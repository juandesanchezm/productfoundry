"""assets stage — generate one PNG per page using the configured image provider.

Pages are generated SEQUENTIALLY, one at a time, and each page must pass the
vision judge before the next one is generated. A page that exhausts
`max_attempts` fails the whole book — no partial book is ever produced.

Quality escalation: the runtime's image policy defines a quality per attempt
(low -> low -> medium for interior pages). Only visual-quality failures escalate;
deterministic failures (wrong size, missing character) correct the prompt and
retry at the same quality.

Reference routing: only the character sheets for characters present on each
page (PageSpec.characters) are passed as image-to-image references — not the
entire roster, reducing input token cost and improving adherence.
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from productfoundry.domain.assets import AssetPlan, AssetSpec
from productfoundry.domain.bible import build_character_bible
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.providers import ImageGenerationRequest
from productfoundry.providers.pricing import image_cost_usd
from productfoundry.stages.audit import _audit_single_image, _is_audit_enabled

PROMPT_VERSION = "assets-v4"


def _get_style_suffixes(pack) -> tuple[str, str]:
    style = (pack.style or {}) if hasattr(pack, "style") else {}
    style = style.get("style", {}) if isinstance(style, dict) else {}
    positive = (style.get("positive_prompt_suffix") or "").strip()
    negative = (style.get("negative_prompt_suffix") or "").strip()
    return positive, negative


def _build_prompt_for_asset(page_prompt: str, positive_suffix: str, negative_suffix: str) -> str:
    parts = []
    if page_prompt:
        parts.append(page_prompt.strip())
    if positive_suffix:
        parts.append(positive_suffix)
    if negative_suffix:
        parts.append(f"Do NOT include: {negative_suffix}")
    return ". ".join(parts) if parts else ""


def build_page_prompt(page, pack) -> str:
    """Build the final image prompt from immutable pack data and page intent."""
    bible = build_character_bible(pack)
    roster = bible.by_id()
    if not page.characters:
        raise RuntimeError(f"{page.id}: no characters declared for page prompt")
    try:
        character_text = "; ".join(
            f"{roster[cid].name_en}: {roster[cid].description_en or roster[cid].archetype_en}"
            for cid in page.characters
        )
    except KeyError as exc:
        raise RuntimeError(f"{page.id}: unknown character {exc.args[0]!r} in prompt") from exc

    canonical = (
        "CANONICAL CHARACTER BIBLE (do not redesign, rename, replace, or add characters): "
        + character_text
    )
    beat = f"STORY BEAT: {page.beat}" if page.beat else ""
    scene = f"PAGE SCENE: {page.prompt}" if page.prompt else ""
    return _build_prompt_for_asset(". ".join(p for p in (canonical, beat, scene) if p), *_get_style_suffixes(pack))


def plan_assets(plan: ProductPlan, image_size: str, pack=None, quality: str = "high") -> AssetPlan:
    positive, negative = _get_style_suffixes(pack) if pack is not None else ("", "")
    assets = []
    for page in plan.pages:
        prompt = build_page_prompt(page, pack) if pack is not None else _build_prompt_for_asset(page.prompt, positive, negative)
        assets.append(
            AssetSpec(
                id=page.id,
                page_id=page.id,
                prompt=prompt,
                aspect_ratio="1:1",
                size=image_size,
                quality=quality,
            )
        )
    return AssetPlan(assets=assets)


def _load_page_references(ctx: StageContext, page) -> list[bytes]:
    """Load character sheet images for only the characters present on this page."""
    if page is None:
        raise RuntimeError("cannot generate a page without a page specification")
    refs: list[bytes] = []
    for char_id in (page.characters or []):
        sheet = ctx.assets_dir / f"character_sheet_{char_id}.png"
        if not sheet.exists() or sheet.stat().st_size == 0:
            raise RuntimeError(f"character sheet missing for {char_id!r}: {sheet}")
        refs.append(sheet.read_bytes())
    return refs


def _generate_one(
    asset: AssetSpec,
    image_provider,
    assets_dir: Path,
    on_cost: callable | None = None,
    reference_images: list[bytes] | None = None,
) -> Path:
    path = assets_dir / f"{asset.id}.png"
    path.write_bytes(
        image_provider.generate(
            ImageGenerationRequest(
                prompt=asset.prompt,
                aspect_ratio=asset.aspect_ratio,
                size=asset.size,
                quality=asset.quality,
                reference_images=reference_images,
            )
        )
    )
    if on_cost is not None:
        on_cost(asset.size, asset.quality)
    return path


def _character_design_hash(pack) -> str:
    import json

    from productfoundry.engine.hashing import sha256_text

    stories = getattr(pack, "stories", None) or {}
    style = getattr(pack, "style", None) or {}
    return sha256_text(json.dumps({"stories": stories, "style": style}, sort_keys=True, default=str))


class AssetsStage(Stage):
    stage_name = "assets"
    inputs: ClassVar = ["concept"]
    outputs: ClassVar = ["assets"]
    input_models: ClassVar = {"concept": ProductPlan}
    prompt_version = PROMPT_VERSION
    provider_key = "image"

    def output_files(self, ctx: StageContext) -> list[Path]:
        return sorted(ctx.assets_dir.glob("page_*.png"))

    def expected_output_files(self, ctx: StageContext) -> list[Path] | None:
        concept_env = ctx.get_artifact("concept")
        if concept_env is None:
            return None
        pages = concept_env.artifact.get("pages", [])
        return [ctx.assets_dir / f"{p.get('id', f'page_{i+1:03d}')}.png" for i, p in enumerate(pages)]

    def extra_hash_inputs(self, ctx: StageContext) -> list[str]:
        # Include character sheet hashes so a sheet change invalidates the cache
        import hashlib

        sheet_hashes = []
        for p in sorted(ctx.assets_dir.glob("character_sheet_*.png")):
            sheet_hashes.append(hashlib.sha256(p.read_bytes()).hexdigest()[:16])
        return sheet_hashes

    def run(self, ctx: StageContext, concept: ProductPlan) -> AssetPlan:
        policies = ctx.runtime.image_policies
        quality_attempts = policies.interior.attempts
        gen_size = ctx.pack.profile.image_size
        plan = plan_assets(concept, gen_size, pack=ctx.pack, quality=quality_attempts[0] if quality_attempts else "low")

        # Regenerate ALL page PNGs when the character's canonical design changed
        design_hash = _character_design_hash(ctx.pack)
        marker = ctx.assets_dir / ".design_hash"
        invalidate = not marker.exists() or marker.read_text().strip() != design_hash
        if invalidate:
            for a in plan.assets:
                path = ctx.assets_dir / f"{a.id}.png"
                if path.exists():
                    path.unlink()
        marker.write_text(design_hash)

        audit_enabled = _is_audit_enabled(ctx.pack)
        for a in plan.assets:
            page = next((p for p in concept.pages if p.id == a.id), None)
            refs = _load_page_references(ctx, page) if page else []
            path = ctx.assets_dir / f"{a.id}.png"
            attempt = 0
            max_attempts = len(quality_attempts) if quality_attempts else 3
            if not path.exists():
                a.quality = quality_attempts[0] if quality_attempts else "low"
                _generate_one(
                    a, ctx.image_provider, ctx.assets_dir,
                    on_cost=lambda size, quality: ctx.set_cost(
                        image_cost_usd(ctx.runtime.image.provider, ctx.runtime.image.model, size, quality)
                    ),
                    reference_images=refs,
                )
            if not audit_enabled:
                a.audit_status = "ok"
                a.audit_notes = "audit disabled"
                continue
            verdict = _audit_single_image(ctx, a, path, page=page)
            while verdict.status != "ok" and attempt < max_attempts:
                attempt += 1
                suggestion = (verdict.rewrite_suggestion or verdict.notes or "").strip()
                if suggestion:
                    a.prompt = f"{a.prompt}. Correction: {suggestion}"
                a.quality = quality_attempts[min(attempt, max_attempts - 1)] if quality_attempts else "medium"
                path.unlink()
                _generate_one(
                    a, ctx.image_provider, ctx.assets_dir,
                    on_cost=lambda size, quality: ctx.set_cost(
                        image_cost_usd(ctx.runtime.image.provider, ctx.runtime.image.model, size, quality)
                    ),
                    reference_images=refs,
                )
                verdict = _audit_single_image(ctx, a, path, page=page)
            a.audit_status = verdict.status
            a.audit_notes = verdict.notes
            if verdict.status != "ok":
                raise RuntimeError(
                    f"page {a.id} failed the judge after {attempt + 1} attempt(s): {verdict.notes}"
                )
        return plan
