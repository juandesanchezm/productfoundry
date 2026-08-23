"""Versioned series contracts and canonical character references."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

IDENTITY_FIELDS = (
    "id",
    "role",
    "name_en",
    "name_es",
    "archetype_en",
    "archetype_es",
    "description_en",
    "description_es",
)


def character_definition_hash(character: dict) -> str:
    """Hash only the identity fields that are locked across a series."""
    payload = {field: character.get(field, "") for field in IDENTITY_FIELDS}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _series_config(pack) -> dict:
    data = getattr(pack, "series", None) or {}
    nested = data.get("series", data) if isinstance(data, dict) else {}
    return nested if isinstance(nested, dict) else {}


def _contracts(pack) -> dict[str, dict]:
    characters = _series_config(pack).get("characters", {})
    if isinstance(characters, list):
        return {c.get("id"): c for c in characters if isinstance(c, dict) and c.get("id")}
    if not isinstance(characters, dict):
        return {}
    return {str(key): value for key, value in characters.items() if isinstance(value, dict)}


def canonical_character_reference(pack, character_id: str) -> Path:
    """Resolve a registered character reference and reject path traversal.

    Franchise catalogs store canonical images in the franchise's
    ``characters/`` directory (``Pack.character_root``); legacy packs store
    them inside the pack directory (``Pack.root``).
    """
    contract = _contracts(pack).get(character_id)
    if contract is None:
        raise ValueError(f"character {character_id!r} is not registered in the series")
    relative = contract.get("reference_image")
    if not relative:
        raise ValueError(f"character {character_id!r} has no canonical reference image")
    root = Path(getattr(pack, "character_root", "") or getattr(pack, "root", ".")).resolve()
    path = (root / str(relative)).resolve()
    if root != path and root not in path.parents:
        raise ValueError(f"character {character_id!r} reference escapes the character directory")
    return path


def validate_series_contract(pack) -> list[str]:
    """Return fail-closed errors for a pack's immutable series roster."""
    config = _series_config(pack)
    if not config:
        return []
    contracts = _contracts(pack)
    roster = (getattr(pack, "stories", None) or {}).get("characters", [])
    roster_by_id = {
        character.get("id"): character
        for character in roster
        if isinstance(character, dict) and character.get("id")
    }
    errors: list[str] = []

    for character_id, character in roster_by_id.items():
        contract = contracts.get(character_id)
        if contract is None:
            errors.append(f"character {character_id!r} is missing from the series registry")
            continue
        expected_hash = contract.get("definition_hash")
        if not expected_hash:
            errors.append(f"character {character_id!r} is missing its locked definition hash")
        elif expected_hash != character_definition_hash(character):
            errors.append(f"character {character_id!r} definition hash changed")

    for character_id in contracts:
        try:
            reference = canonical_character_reference(pack, character_id)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not reference.exists() or reference.stat().st_size == 0:
            errors.append(f"character {character_id!r} canonical reference image is missing: {reference}")

    return errors
