"""pack_validate stage — deterministic fail-closed validation of the pack.

Runs before any generation: the roster (Character Bible), the stories and
the page plan must be internally consistent, otherwise the pipeline stops
before spending a single dollar on images.
"""
from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from productfoundry.domain.bible import (
    build_character_bible,
    validate_character_bible,
    validate_page_plan,
    validate_story_characters,
)
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.hashing import sha256_json
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.series import validate_series_contract


class PackValidationReport(BaseModel):
    verdict: str = "fail"  # pass | fail
    errors: list[str] = Field(default_factory=list)
    character_count: int = 0
    main_character: str = ""


def validate_forbidden_marketing_values(pack) -> list[str]:
    """Fail closed when the pack still carries a legacy marketing value that
    must never reach customers (old character names, old author, old series).

    The forbidden values are declared by the pack itself
    (`compliance.forbidden_marketing_values`), keeping the engine
    niche-agnostic: it only enforces whatever strings the pack lists.
    """
    compliance = (getattr(pack, "compliance", None) or {}).get("compliance", {}) or {}
    markers = compliance.get("forbidden_marketing_values", []) or []
    if not markers:
        return []
    errors: list[str] = []
    haystack: list[str] = [str(getattr(pack.profile, "author", "")), str(getattr(pack.profile, "series_name", ""))]
    stories = getattr(pack, "stories", None) or {}
    if isinstance(stories, dict):
        roster = stories.get("characters", [])
        for character in roster if isinstance(roster, list) else []:
            if isinstance(character, dict):
                haystack.append(" ".join(str(character.get(k, "")) for k in ("name_en", "name_es")))
        story_list = stories.get("stories", [])
        for story in story_list if isinstance(story_list, list) else []:
            if isinstance(story, dict):
                for key in ("title_en", "title_es", "subtitle_en", "subtitle_es"):
                    haystack.append(str(story.get(key, "")))
    for marker in markers:
        for text in haystack:
            if str(marker).casefold() in text.casefold():
                errors.append(f"forbidden marketing value {marker!r} found in pack metadata")
    return errors


class PackValidationStage(Stage):
    stage_name = "pack_validate"
    inputs: ClassVar = ["concept"]
    outputs: ClassVar = ["pack_validate"]
    input_models: ClassVar = {"concept": ProductPlan}
    prompt_version = "pack-validate-v2"
    gate_verdict = "pass"

    def extra_hash_inputs(self, ctx: StageContext) -> list[str]:
        return [sha256_json(ctx.pack.series)] if ctx.pack.series else []

    def run(self, ctx: StageContext, concept: ProductPlan) -> PackValidationReport:
        bible = build_character_bible(ctx.pack)
        errors: list[str] = []
        errors.extend(validate_series_contract(ctx.pack))
        errors.extend(validate_forbidden_marketing_values(ctx.pack))
        allowed_ids: set[str] | None = None
        # Only enforce character-bible rules when the pack declares a roster.
        # Roster-less packs (e.g. the generic example pack) use freeform/theme
        # mode and must not be rejected for lacking a main character.
        if bible.characters:
            errors.extend(validate_character_bible(bible))
            errors.extend(validate_story_characters(ctx.pack, bible))
            if ctx.request.story_id:
                from productfoundry.stages.concept import _lookup_story

                story = _lookup_story(ctx.pack, ctx.request.story_id)
                if story is not None:
                    allowed_ids = set(story.get("characters_present", []) or [])
            errors.extend(validate_page_plan(concept, bible, allowed_ids=allowed_ids))
        # Always validate page ID uniqueness (independent of roster)
        seen_ids: set[str] = set()
        for page in concept.pages:
            if not page.id:
                errors.append(f"page at index {page.index}: empty id")
            elif page.id in seen_ids:
                errors.append(f"duplicate page id: {page.id}")
            else:
                seen_ids.add(page.id)
        # Validate page count
        if len(concept.pages) != ctx.request.page_count:
            errors.append(
                f"page count mismatch: concept has {len(concept.pages)}, "
                f"request has {ctx.request.page_count}"
            )
        if "print" in ctx.request.formats and ctx.request.page_count < 24:
            errors.append(
                f"KDP minimum is 24 pages for print products; request has {ctx.request.page_count}"
            )
        # Validate story_id if set
        if ctx.request.story_id:
            from productfoundry.stages.concept import _lookup_story
            if _lookup_story(ctx.pack, ctx.request.story_id) is None:
                errors.append(f"story_id {ctx.request.story_id!r} not found in pack stories")
        main = bible.main()
        return PackValidationReport(
            verdict="pass" if not errors else "fail",
            errors=errors,
            character_count=len(bible.characters),
            main_character=main.id if main else "",
        )
