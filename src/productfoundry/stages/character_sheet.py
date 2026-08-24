"""character_sheet stage — make canonical reference images available for all roster characters.

The roster lives in the pack (stories.yaml `characters`); the engine only
reads it generically.

For franchise catalogs the canonical PNGs live in the franchise's
``characters/`` directory. This stage does NOT copy them into the edition:
every consumer reads the canonical image directly, so there is a single
source of truth on disk. The stage merely declares the roster assets so the
audit stage can gate them. Only legacy packs without canonical characters
generate a sheet from scratch (``assets/character_sheet_<id>.png``).
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from productfoundry.domain.assets import AssetPlan, AssetSpec
from productfoundry.engine.cost_tracking import estimate_image_cost
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.providers import ImageGenerationRequest
from productfoundry.series import canonical_character_reference

PROMPT_VERSION = "character-sheet-v3"

_SHEET_SUFFIX = "_sheet.png"


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
    """Compose the reference-sheet prompt for a character (legacy packs only)."""
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
    """Legacy generated sheet path (only used by packs without canonical chars)."""
    return assets_dir / f"character_sheet_{char_id}.png"


def _main_sheet_path(assets_dir: Path) -> Path:
    """Legacy path for backward compatibility (main character only)."""
    return assets_dir / "character_sheet.png"


class CharacterSheetStage(Stage):
    stage_name = "character_sheet"
    inputs: ClassVar = []
    outputs: ClassVar = ["character_sheet"]
    prompt_version = PROMPT_VERSION
    provider_key = "image"

    def output_files(self, ctx: StageContext) -> list[Path]:
        """Files this stage produces. Canonical characters produce none; the
        audit gate consumes the canonical PNG directly from the catalog."""
        roster = _roster(ctx.pack)
        if not roster:
            return []
        return [
            _sheet_path(ctx.assets_dir, c["id"])
            for c in roster
            if c.get("id") and _has_canonical(ctx.pack, c["id"]) is False
        ]

    def expected_output_files(self, ctx: StageContext) -> list[Path] | None:
        return self.output_files(ctx)

    def extra_hash_inputs(self, ctx: StageContext) -> list[str]:
        import hashlib

        hashes: list[str] = []
        for character in _roster(ctx.pack):
            character_id = character.get("id")
            if not character_id:
                continue
            try:
                reference = canonical_character_reference(ctx.pack, character_id)
            except ValueError:
                continue
            if reference.exists():
                hashes.append(hashlib.sha256(reference.read_bytes()).hexdigest()[:16])
        return hashes

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
            try:
                canonical = canonical_character_reference(ctx.pack, char_id)
            except ValueError:
                canonical = None
            if canonical is not None:
                # Canonical source of truth: do not duplicate. Consumers read
                # the catalog PNG directly; nothing is written to the edition.
                if not canonical.exists() or canonical.stat().st_size == 0:
                    raise RuntimeError(f"canonical character reference is missing: {canonical}")
            elif not out_path.exists():
                out_path.parent.mkdir(parents=True, exist_ok=True)
                image_request = ImageGenerationRequest(
                    prompt=prompt,
                    aspect_ratio="1:1",
                    size=gen_size,
                    quality=quality,
                )
                out_path.write_bytes(ctx.image_provider.generate(image_request))
                ctx.set_cost(
                    estimate_image_cost(
                        ctx.runtime.image.provider,
                        ctx.runtime.image.model,
                        gen_size,
                        quality,
                        getattr(image_request, "usage", None) or {},
                    )
                )
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


def _has_canonical(pack, character_id: str) -> bool:
    try:
        ref = canonical_character_reference(pack, character_id)
    except ValueError:
        return False
    return ref.exists() and ref.stat().st_size > 0
