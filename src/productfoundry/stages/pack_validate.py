"""pack_validate stage — deterministic fail-closed validation of the pack.

Runs before any generation: the roster (Character Bible), the stories and
the page plan must be internally consistent, otherwise the pipeline stops
before spending a single dollar on images.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from productfoundry.domain.bible import (
    build_character_bible,
    validate_character_bible,
    validate_page_plan,
    validate_story_characters,
)
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.pipeline import Stage, StageContext


class PackValidationReport(BaseModel):
    verdict: str = "fail"  # pass | fail
    errors: list[str] = Field(default_factory=list)
    character_count: int = 0
    main_character: str = ""


class PackValidationStage(Stage):
    stage_name = "pack_validate"
    inputs = ["concept"]
    outputs = ["pack_validate"]
    input_models = {"concept": ProductPlan}
    prompt_version = "pack-validate-v1"
    gate_verdict = "pass"

    def run(self, ctx: StageContext, concept: ProductPlan) -> PackValidationReport:
        bible = build_character_bible(ctx.pack)
        errors: list[str] = []
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
