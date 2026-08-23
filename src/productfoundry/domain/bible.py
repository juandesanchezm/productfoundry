"""Character Bible — the canonical, validated character roster of a pack.

The engine never invents characters: every character that may appear in a
book must be declared here (stories.yaml `characters`), and every page must
reference characters by their stable ID. Validation is deterministic and
fail-closed: a pack whose roster is inconsistent cannot produce a book.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from productfoundry.domain.product import Character, ProductPlan


class CharacterBible(BaseModel):
    characters: list[Character] = Field(default_factory=list)

    def by_id(self) -> dict[str, Character]:
        return {c.id: c for c in self.characters}

    def main(self) -> Character | None:
        for c in self.characters:
            if c.role == "main":
                return c
        return None


def build_character_bible(pack) -> CharacterBible:
    """Read the roster from pack.stories.characters (stories.yaml)."""
    stories = getattr(pack, "stories", None) or {}
    roster = stories.get("characters", []) if isinstance(stories, dict) else []
    characters = []
    for c in roster:
        if isinstance(c, dict):
            characters.append(Character.model_validate(c))
    return CharacterBible(characters=characters)


def normalize_character_ids(pack, values: list[str]) -> list[str]:
    """Normalize LLM-emitted IDs or display names to stable roster IDs.

    The model may return a display name instead of its slug even when the schema
    asks for IDs. That translation is safe only against the declared roster;
    unknown names remain a hard error so the engine never invents a character.
    """
    bible = build_character_bible(pack)
    aliases: dict[str, str] = {}
    for character in bible.characters:
        for alias in (character.id, character.name_en, character.name_es):
            if alias:
                aliases.setdefault(alias.strip().casefold(), character.id)

    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"invalid character reference: {value!r}")
        character_id = aliases.get(value.strip().casefold())
        if character_id is None:
            raise ValueError(f"unknown character reference: {value!r}")
        normalized.append(character_id)
    return normalized


def validate_character_bible(bible: CharacterBible) -> list[str]:
    """Deterministic roster validation. Returns a list of errors (empty = valid).

    Rules:
    - exactly one character with role == "main"
    - unique character IDs
    - every character has a non-empty id and name_en
    """
    errors: list[str] = []
    ids = [c.id for c in bible.characters]
    if len(ids) != len(set(ids)):
        seen: set[str] = set()
        for cid in ids:
            if cid in seen:
                errors.append(f"duplicate character id: {cid!r}")
            seen.add(cid)
    mains = [c for c in bible.characters if c.role == "main"]
    if len(mains) != 1:
        errors.append(f"exactly one 'main' character required, found {len(mains)}")
    for c in bible.characters:
        if not c.id:
            errors.append("character with empty id")
        if not c.name_en:
            errors.append(f"character {c.id!r} missing name_en")
    return errors


def validate_story_characters(pack, bible: CharacterBible) -> list[str]:
    """Deterministic story validation. Returns a list of errors (empty = valid).

    Rules:
    - every story's `characters_present` must reference declared roster IDs
    - every story must include the main character
    """
    errors: list[str] = []
    stories = (getattr(pack, "stories", None) or {})
    if not isinstance(stories, dict):
        return errors
    roster_ids = set(bible.by_id())
    main_id = bible.main().id if bible.main() else None
    for s in stories.get("stories", []) or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("id", "?")
        present = s.get("characters_present", []) or []
        for cid in present:
            if cid not in roster_ids:
                errors.append(f"story {sid!r}: character_present {cid!r} not in roster")
        if main_id and main_id not in present:
            errors.append(f"story {sid!r}: main character {main_id!r} missing from characters_present")
    return errors


def validate_page_plan(
    plan: ProductPlan,
    bible: CharacterBible,
    allowed_ids: set[str] | None = None,
) -> list[str]:
    """Deterministic page-plan validation. Returns a list of errors (empty = valid).

    Rules:
    - the main character appears in EVERY page
    - every page references only declared roster IDs
    - no duplicate character IDs within a page
    - when ``allowed_ids`` is provided, pages may only use that story cast
    """
    errors: list[str] = []
    roster_ids = set(bible.by_id())
    main_id = bible.main().id if bible.main() else None
    used: set[str] = set()
    for page in plan.pages:
        seen: set[str] = set()
        for cid in page.characters:
            if cid in seen:
                errors.append(f"{page.id}: duplicate character {cid!r}")
            seen.add(cid)
            if cid not in roster_ids:
                errors.append(f"{page.id}: unknown character {cid!r} (not in roster)")
            elif allowed_ids is not None and cid not in allowed_ids:
                errors.append(f"{page.id}: character {cid!r} not allowed in this story")
            used.add(cid)
        if main_id and main_id not in page.characters:
            errors.append(f"{page.id}: main character {main_id!r} missing")
    if main_id and main_id not in used:
        errors.append(f"main character {main_id!r} appears in no page")
    return errors
