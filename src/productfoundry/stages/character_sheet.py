"""character_sheet stage — generate canonical reference images for all roster characters.

The main character sheet is used as the image-to-image reference for every
interior page and the cover hero. Supporting character sheets are used only
on pages that include them. The roster lives in the pack (stories.yaml
`characters`); the engine only reads it generically.
"""
from __future__ import annotations

from pathlib import Path

from productfoundry.domain.assets import AssetPlan, AssetSpec
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.providers import ImageGenerationRequest
from productfoundry.providers.pricing import image_cost_usd

PROMPT_VERSION = "character-sheet-v2"


def _roster(pack) -> list[dict]:
    """Return all roster characters from stories.yaml."""
    stories = (getattr(pack, "stories", None) or {})
    if not isinstance(stories, dict):
        return []
    roster = stories.get("characters", [])
    if not isinstance(roster, list):
        return []
    return [c for c in roster if isinstance(c, dict)]


def _main_character(pack) -> dict | None:
    """Return the pack's main character (role == 'main') from stories.yaml."""
    for c in _roster(pack):
        if c.get("role") == "main":
            return c
    return None


def _build_sheet_prompt(pack, character: dict) -> str:
    """Compose the canonical reference-sheet prompt for a character."""
    style = pack.style.get("style", {}) if hasattr(pack, "style") else {}
    custom = (style.get("character_sheet_prompt") or "").strip()
    if custom:
        return custom
    desc = character.get("description_en") or character.get("archetype_en") or "the character"
    return (
        f"Canonical character reference sheet of {desc}. "
        "Full body, front view, standing in a neutral relaxed pose, arms relaxed at sides, "
        "facing the viewer, simple flat white background, black and white line art, "
        "thick clean outlines, rounded soft shapes, no text, no letters, no watermark, no signature."
    )


def _sheet_path(assets_dir: Path, char_id: str) -> Path:
    return assets_dir / f"character_sheet_{char_id}.png"


def _main_sheet_path(assets_dir: Path) -> Path:
    """Legacy path for backward compatibility (main character only)."""
    return assets_dir / "character_sheet.png"


class CharacterSheetStage(Stage):
    stage_name = "character_sheet"
    inputs = []
    outputs = ["character_sheet"]
    prompt_version = PROMPT_VERSION
    provider_key = "image"

    def output_files(self, ctx: StageContext) -> list[Path]:
        roster = _roster(ctx.pack)
        if not roster:
            return []
        return [_sheet_path(ctx.assets_dir, c["id"]) for c in roster if c.get("id")]

    def expected_output_files(self, ctx: StageContext) -> list[Path] | None:
        return self.output_files(ctx)

    def run(self, ctx: StageContext) -> AssetPlan:
        roster = _roster(ctx.pack)
        if not roster:
            return AssetPlan(assets=[])

        policies = ctx.runtime.image_policies
        quality_attempts = policies.character_sheet.attempts
        gen_size = ctx.pack.profile.image_size

        assets: list[AssetSpec] = []
        for character in roster:
            char_id = character.get("id")
            if not char_id:
                continue
            prompt = _build_sheet_prompt(ctx.pack, character)
            quality = quality_attempts[0] if quality_attempts else "medium"
            out_path = _sheet_path(ctx.assets_dir, char_id)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if not out_path.exists():
                out_path.write_bytes(
                    ctx.image_provider.generate(
                        ImageGenerationRequest(
                            prompt=prompt,
                            aspect_ratio="1:1",
                            size=gen_size,
                            quality=quality,
                        )
                    )
                )
                ctx.set_cost(
                    image_cost_usd(
                        ctx.runtime.image.provider,
                        ctx.runtime.image.model,
                        gen_size,
                        quality,
                    )
                )
            # Also maintain the legacy main-character sheet path
            if character.get("role") == "main":
                legacy = _main_sheet_path(ctx.assets_dir)
                if not legacy.exists() or legacy.read_bytes() != out_path.read_bytes():
                    legacy.write_bytes(out_path.read_bytes())
            spec = AssetSpec(
                id=f"character_sheet_{char_id}",
                page_id=f"character_sheet_{char_id}",
                prompt=prompt,
                aspect_ratio="1:1",
                size=gen_size,
                quality=quality,
            )
            assets.append(spec)
        return AssetPlan(assets=assets)