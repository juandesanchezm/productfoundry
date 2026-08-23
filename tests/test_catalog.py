"""Catalog tests — franchise layout, character contracts, edition paths."""
from pathlib import Path

import pytest

from productfoundry.catalog import (
    CatalogError,
    edition_dir_for,
    load_franchise,
    resolve_book,
)
from productfoundry.series import validate_series_contract

ROOT = Path(__file__).resolve().parents[1]


def test_load_franchise_cocholate():
    franchise = load_franchise(ROOT / "projects" / "cocholate")

    assert franchise.id == "cocholate"
    assert set(franchise.characters) == {"cocholate", "pip", "pebble", "clover"}
    assert "coloring-fantasy" in franchise.packs
    assert "cocholate-adventures" in franchise.series


def test_franchise_characters_preserved_byte_for_byte():
    franchise = load_franchise(ROOT / "projects" / "cocholate")
    old = ROOT / "packs" / "coloring-fantasy" / "characters"
    if not old.exists():
        pytest.skip("legacy character directory removed")
    for char_id, character in franchise.characters.items():
        legacy_name = "main_dragon" if char_id == "cocholate" else char_id
        legacy = old / f"{legacy_name}.png"
        if legacy.exists():
            assert character.reference_image.read_bytes() == legacy.read_bytes()


def test_resolve_book_synthesizes_pack_contract():
    bundle = resolve_book(
        ROOT / "projects" / "cocholate", "cocholate-adventures", "magical-day", "coloring-fantasy"
    )

    assert bundle.pack.profile.author == "Noa Bloom"
    assert bundle.pack.profile.series_name["en"] == "Cocholate's Adventures"
    assert bundle.pack.stories["stories"][0]["id"] == "magical-day"
    assert bundle.pack.stories["stories"][0]["title_en"] == "Cocholate's Magical Day"
    assert len(bundle.pack.stories["stories"][0]["arc"]) == 24
    main = next(c for c in bundle.pack.stories["characters"] if c["role"] == "main")
    assert main["id"] == "cocholate"
    assert main["name_en"] == "Cocholate"
    assert main["name_es"] == "Cocholate"


def test_series_contract_validates_canonical_references():
    bundle = resolve_book(
        ROOT / "projects" / "cocholate", "cocholate-adventures", "magical-day", "coloring-fantasy"
    )

    assert validate_series_contract(bundle.pack) == []
    reference = bundle.pack.character_root / "cocholate.png"
    assert reference.exists()
    assert reference.stat().st_size > 0


def test_all_books_are_24_pages():
    for book_id in ("magical-day", "tea-party", "forest-explorer"):
        bundle = resolve_book(
            ROOT / "projects" / "cocholate", "cocholate-adventures", book_id, "coloring-fantasy"
        )
        story = bundle.pack.stories["stories"][0]
        assert story["pages"] == 24
        assert len(story["arc"]) == 24


def test_edition_dir_is_nested_under_series_books():
    path = edition_dir_for(ROOT / "projects" / "cocholate", "cocholate-adventures", "magical-day", "coloring-fantasy")
    assert path == (
        ROOT / "projects" / "cocholate" / "series" / "cocholate-adventures" / "books" / "magical-day" / "editions" / "coloring-fantasy"
    )


def test_resolve_unknown_pack_raises():
    with pytest.raises(CatalogError, match="pack"):
        resolve_book(ROOT / "projects" / "cocholate", "cocholate-adventures", "magical-day", "nonexistent")


def test_resolve_unknown_series_raises():
    with pytest.raises(CatalogError, match="series"):
        resolve_book(ROOT / "projects" / "cocholate", "nonexistent", "magical-day", "coloring-fantasy")


def test_resolve_unknown_book_raises():
    with pytest.raises(CatalogError, match="book"):
        resolve_book(ROOT / "projects" / "cocholate", "cocholate-adventures", "nonexistent", "coloring-fantasy")
