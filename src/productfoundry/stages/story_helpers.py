"""Shared localized story metadata helpers."""
from __future__ import annotations


def localized_story_subtitle(pack, story_id: str, language: str, fallback: str = "") -> str:
    stories = (getattr(pack, "stories", None) or {}).get("stories", [])
    for story in stories:
        if not isinstance(story, dict) or story.get("id") != story_id:
            continue
        localized = story.get(f"subtitle_{language}")
        if localized:
            return str(localized)
        if story.get("subtitle_en"):
            return str(story["subtitle_en"])
    return fallback


def localized_series_name(pack, language: str, default: str = "") -> str:
    """Return the series name in the requested language.

    Accepts either a plain string (legacy packs) or a {language: name}
    mapping declared in the pack profile.
    """
    series = getattr(pack, "series_name", None) or getattr(
        getattr(pack, "profile", None), "series_name", None
    ) or ""
    if isinstance(series, dict):
        return str(series.get(language) or series.get("en") or default)
    return str(series) if series else default
