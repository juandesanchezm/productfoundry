"""back_cover stage — generate the back-cover background artwork.

Produces a SINGLE soft background shared by every language (identical
artwork across locales). It deliberately contains NO character and NO text:
the back cover of an activity product shows interior-page thumbnails
and a calm area where KDP will place its ISBN barcode automatically.
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from productfoundry.domain.assets import AssetPlan, AssetSpec
from productfoundry.engine.cost_tracking import estimate_image_cost
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.providers import ImageGenerationRequest
from productfoundry.stages.audit import _audit_single_image, _is_audit_enabled
from productfoundry.stages.hero import _get_author

PROMPT_VERSION = "back-cover-v2"


def build_back_cover_prompt(pack) -> str:
    style = pack.style.get("style", {}) if hasattr(pack, "style") else {}
    positive = (style.get("positive_prompt_suffix") or "").strip()
    negative = (style.get("negative_prompt_suffix") or "").strip()
    scene_hint = ""
    if hasattr(pack, "themes") and isinstance(pack.themes, dict):
        scene_hint = (pack.themes.get("back_cover_scene") or "").strip()
    if not scene_hint:
        theme = (getattr(pack, "profile", None).theme if hasattr(pack, "profile") else "") or ""
        scene_hint = f"{theme} scenery background".strip()

    parts = []
    if positive:
        parts.append(positive.replace("black and white line art", "vibrant illustration"))
    else:
        parts.append("vibrant illustration")
    parts.append(
        f"{scene_hint} background illustration for a book back cover. "
        "No characters, no animals, no figures of any kind: only scenery. "
        "The upper area is calm and clean to host interior-page thumbnails, "
        "the lower area is quiet and mostly empty. No text, no letters, no "
        "sign, no banner, no watermark, no signature anywhere in the image."
    )
    parts.append("vibrant saturated colors, soft pastel palette, gentle dreamy atmosphere")
    parts.append("high quality, illustration, large format poster art")
    if negative:
        cleaned = (
            negative.replace("color", "")
            .replace("black and white", "")
            .replace("text", "")
            .replace("signature", "")
        )
        parts.append(f"Do NOT include: {cleaned}")
    return ". ".join(parts)


def generate_back_cover(
    prompt: str,
    image_provider,
    image_size: str,
    out_path: Path,
    on_cost=None,
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


class BackCoverStage(Stage):
    stage_name = "back_cover"
    inputs: ClassVar = []
    outputs: ClassVar = ["back_cover"]
    input_models: ClassVar = {}
    prompt_version = PROMPT_VERSION
    provider_key = "image"
    max_attempts = 3

    def output_files(self, ctx: StageContext) -> list[Path]:
        # One shared background for every language (identical across locales).
        return [ctx.assets_dir / "back_cover.png"]

    def expected_output_files(self, ctx: StageContext) -> list[Path] | None:
        return self.output_files(ctx)

    def extra_hash_inputs(self, ctx: StageContext) -> list[str]:
        return []

    def run(self, ctx: StageContext) -> AssetPlan:
        gen_size = ctx.pack.profile.image_size
        back_path = ctx.assets_dir / "back_cover.png"
        prompt = build_back_cover_prompt(ctx.pack)
        if not back_path.exists():
            generate_back_cover(
                prompt, ctx.image_provider, gen_size, back_path,
                on_cost=lambda size, quality, usage: ctx.set_cost(
                    estimate_image_cost(ctx.runtime.image.provider, ctx.runtime.image.model, size, quality, usage)
                ),
            )
        spec = AssetSpec(
            id="back_cover", page_id="back_cover", prompt=prompt,
            aspect_ratio="1:1", size=gen_size, quality="high",
            expected_author=_get_author(ctx.pack),
        )
        if not _is_audit_enabled(ctx.pack):
            spec.audit_status = "ok"
            spec.audit_notes = "audit disabled"
            return AssetPlan(assets=[spec])
        verdict = _audit_single_image(ctx, spec, back_path, back_mode=True)
        attempt = 1
        while verdict.status != "ok" and attempt < self.max_attempts:
            suggestion = (verdict.rewrite_suggestion or verdict.notes or "").strip()
            if suggestion:
                spec.prompt = f"{spec.prompt}. Correction: {suggestion}"
            back_path.unlink()
            generate_back_cover(
                spec.prompt, ctx.image_provider, gen_size, back_path,
                on_cost=lambda s, q, usage: ctx.set_cost(
                    estimate_image_cost(ctx.runtime.image.provider, ctx.runtime.image.model, s, q, usage)
                ),
            )
            verdict = _audit_single_image(ctx, spec, back_path, back_mode=True)
            attempt += 1
        if verdict.status != "ok":
            raise RuntimeError(
                f"back cover failed the judge after {attempt} attempt(s): {verdict.notes}"
            )
        spec.audit_status = verdict.status
        spec.audit_notes = verdict.notes
        return AssetPlan(assets=[spec])
