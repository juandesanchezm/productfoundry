"""Franchise catalog loader.

A franchise is a top-level directory (e.g. ``cocholate/``) that owns:

- ``characters/`` — canonical characters (yaml definition + canonical PNG)
- ``packs/`` — reusable production recipes (pack.yaml + aux files)
- ``series/`` — series of books; each book lives under ``series/<id>/books/``
  and each concrete execution under ``series/<id>/books/<book>/editions/<pack>/``

The engine resolves a (franchise, series, book, pack) tuple into a
:class:`ResolvedBundle` that synthesizes the legacy ``Pack`` contract
consumed by the pipeline stages, so the stages stay niche-agnostic.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from productfoundry.pack_loader import Pack, PackError, _read_yaml
from productfoundry.series import character_definition_hash


class CatalogError(Exception):
    pass


@dataclass
class Character:
    data: dict

    @property
    def id(self) -> str:
        return str(self.data.get("id", ""))

    @property
    def reference_image(self) -> Path:
        rel = self.data.get("reference_image") or f"{self.id}.png"
        return self.root / str(rel)

    root: Path = field(default_factory=Path)


@dataclass
class PackBundle:
    profile: object  # PackProfile
    root: Path
    style: dict = field(default_factory=dict)
    packaging: dict = field(default_factory=dict)
    listing: dict = field(default_factory=dict)
    quality: dict = field(default_factory=dict)
    audit: dict = field(default_factory=dict)
    compliance: dict = field(default_factory=dict)
    themes: dict = field(default_factory=dict)


@dataclass
class Book:
    data: dict
    root: Path

    @property
    def id(self) -> str:
        return str(self.data.get("id", ""))


@dataclass
class Series:
    id: str
    root: Path
    config: dict
    books: list[Book] = field(default_factory=list)

    def book(self, book_id: str) -> Book:
        for book in self.books:
            if book.id == book_id:
                return book
        raise CatalogError(f"book {book_id!r} not found in series {self.id!r}")


@dataclass
class Franchise:
    id: str
    root: Path
    characters: dict[str, Character] = field(default_factory=dict)
    packs: dict[str, PackBundle] = field(default_factory=dict)
    series: dict[str, Series] = field(default_factory=dict)

    def pack(self, pack_id: str) -> PackBundle:
        try:
            return self.packs[pack_id]
        except KeyError:
            raise CatalogError(
                f"pack {pack_id!r} not found in franchise {self.id!r} "
                f"(available: {', '.join(sorted(self.packs)) or 'none'})"
            ) from None

    def series_by_id(self, series_id: str) -> Series:
        try:
            return self.series[series_id]
        except KeyError:
            raise CatalogError(
                f"series {series_id!r} not found in franchise {self.id!r} "
                f"(available: {', '.join(sorted(self.series)) or 'none'})"
            ) from None


def _read_mapping(path: Path) -> dict:
    return _read_yaml(path)


def load_franchise(franchise_dir: Path) -> Franchise:
    """Load a franchise directory: characters, packs and series."""
    franchise_dir = franchise_dir.resolve()
    if not franchise_dir.is_dir():
        raise CatalogError(f"franchise directory not found: {franchise_dir}")
    franchise_id = franchise_dir.name
    franchise = Franchise(id=franchise_id, root=franchise_dir)

    char_dir = franchise_dir / "characters"
    if char_dir.is_dir():
        for path in sorted(char_dir.glob("*.yaml")):
            data = _read_mapping(path)
            char_id = str(data.get("id", ""))
            if not char_id:
                raise CatalogError(f"character {path} is missing an id")
            if char_id in franchise.characters:
                raise CatalogError(f"duplicate character id {char_id!r}")
            franchise.characters[char_id] = Character(data=data, root=char_dir)
    else:
        # backward-compatible single-directory name
        char_dir = franchise_dir / "character"
        if char_dir.is_dir():
            for path in sorted(char_dir.glob("*.yaml")):
                data = _read_mapping(path)
                char_id = str(data.get("id", ""))
                if not char_id:
                    raise CatalogError(f"character {path} is missing an id")
                if char_id in franchise.characters:
                    raise CatalogError(f"duplicate character id {char_id!r}")
                franchise.characters[char_id] = Character(data=data, root=char_dir)

    packs_dir = franchise_dir / "packs"
    if packs_dir.is_dir():
        for pack_dir in sorted(packs_dir.iterdir()):
            if not pack_dir.is_dir() or not (pack_dir / "pack.yaml").exists():
                continue
            franchise.packs[pack_dir.name] = _load_pack_bundle(pack_dir)

    series_dir = franchise_dir / "series"
    if series_dir.is_dir():
        for sdir in sorted(series_dir.iterdir()):
            if not sdir.is_dir() or not (sdir / "series.yaml").exists():
                continue
            config = _read_mapping(sdir / "series.yaml")
            nested = config.get("series", config)
            if not isinstance(nested, dict):
                raise CatalogError(f"series {sdir.name}: expected a mapping")
            series_id = str(nested.get("id", sdir.name))
            if series_id != sdir.name:
                raise CatalogError(
                    f"series directory {sdir.name!r} does not match declared id {series_id!r}"
                )
            series = Series(id=series_id, root=sdir, config=config)
            books_dir = sdir / "books"
            if books_dir.is_dir():
                for bdir in sorted(books_dir.iterdir()):
                    if not bdir.is_dir() or not (bdir / "book.yaml").exists():
                        continue
                    book_data = _read_mapping(bdir / "book.yaml")
                    book_id = str(book_data.get("id", bdir.name))
                    if book_id != bdir.name:
                        raise CatalogError(
                            f"book directory {bdir.name!r} does not match declared id {book_id!r}"
                        )
                    series.books.append(Book(data=book_data, root=bdir))
            franchise.series[series_id] = series

    if not franchise.characters:
        raise CatalogError(f"franchise {franchise_id!r} has no characters in characters/")
    if not franchise.packs:
        raise CatalogError(f"franchise {franchise_id!r} has no packs in packs/")
    if not franchise.series:
        raise CatalogError(f"franchise {franchise_id!r} has no series in series/")
    return franchise


def _load_pack_bundle(pack_dir: Path) -> PackBundle:
    base = _read_mapping(pack_dir / "pack.yaml")
    from productfoundry.domain.pack import PackProfile

    try:
        profile = PackProfile.model_validate(base)
    except Exception as e:
        raise PackError(f"invalid pack profile in {pack_dir}: {e}") from e
    aux: dict[str, dict] = {}
    for name in ("style", "themes", "packaging", "listing", "quality", "audit", "compliance"):
        path = pack_dir / f"{name}.yaml"
        if path.exists():
            aux[name] = _read_mapping(path)
    return PackBundle(profile=profile, root=pack_dir, **aux)


@dataclass
class ResolvedBundle:
    franchise: Franchise
    series: Series
    book: Book
    pack: Pack
    edition_dir: Path


def resolve_book(
    franchise_dir: Path,
    series_id: str,
    book_id: str,
    pack_id: str,
) -> ResolvedBundle:
    """Resolve a (series, book, pack) into the synthesized Pack and edition dir."""
    franchise = load_franchise(franchise_dir)
    series = franchise.series_by_id(series_id)
    book = series.book(book_id)
    bundle = franchise.pack(pack_id)

    roster = [copy.deepcopy(c.data) for c in franchise.characters.values()]
    if not any(c.get("role") == "main" for c in roster):
        raise CatalogError(f"franchise {franchise.id!r} has no main character")

    series_config = copy.deepcopy(series.config)
    raw_series = series_config.get("series") if isinstance(series_config, dict) else None
    if not isinstance(raw_series, dict):
        raise CatalogError(f"series {series_id!r}: expected a mapping under 'series'")
    nested = raw_series
    declared_contracts = nested.get("characters") or {}
    if isinstance(declared_contracts, list):
        declared_contracts = {
            entry.get("id"): entry for entry in declared_contracts if isinstance(entry, dict) and entry.get("id")
        }
    if not isinstance(declared_contracts, dict):
        raise CatalogError(f"series {series_id!r}: characters must be a mapping")
    declared_ids = {cid for cid in declared_contracts if isinstance(cid, str)}
    franchise_ids = set(franchise.characters)
    extra_in_franchise = franchise_ids - declared_ids
    missing_in_franchise = declared_ids - franchise_ids
    if extra_in_franchise:
        raise CatalogError(
            f"series {series_id!r} declares fewer characters than franchise: "
            f"missing {sorted(extra_in_franchise)}"
        )
    if missing_in_franchise:
        raise CatalogError(
            f"series {series_id!r} declares characters not present in franchise: "
            f"{sorted(missing_in_franchise)}"
        )
    contracts: dict[str, dict] = {}
    palettes: dict[str, dict] = {}
    for char_id, character in franchise.characters.items():
        contract = declared_contracts.get(char_id) or {}
        expected_hash = contract.get("definition_hash")
        if not expected_hash:
            raise CatalogError(
                f"series {series_id!r}: character {char_id!r} is missing its locked definition hash"
            )
        if expected_hash != character_definition_hash(character.data):
            raise CatalogError(
                f"series {series_id!r}: character {char_id!r} definition hash changed "
                "(bump series.version to register a new design)"
            )
        reference_image = contract.get("reference_image") or f"{char_id}.png"
        contracts[char_id] = {
            "reference_image": reference_image,
            "definition_hash": expected_hash,
        }
        palettes[char_id] = {
            lang: (character.data.get(f"palette_{lang}") or "")
            for lang in ("en", "es")
        }
    nested["characters"] = contracts

    pack = Pack(
        profile=bundle.profile,
        root=bundle.root,
        character_root=franchise.root / "characters",
        style=bundle.style,
        themes=bundle.themes,
        packaging=bundle.packaging,
        listing=bundle.listing,
        quality=bundle.quality,
        audit=bundle.audit,
        compliance=bundle.compliance,
        stories={"characters": roster, "stories": [book.data]},
        series=series_config,
        palettes=palettes,
    )

    edition_dir = (
        franchise.root / "series" / series_id / "books" / book_id / "editions" / pack_id
    )
    return ResolvedBundle(
        franchise=franchise,
        series=series,
        book=book,
        pack=pack,
        edition_dir=edition_dir,
    )


def edition_dir_for(
    franchise_dir: Path,
    series_id: str,
    book_id: str,
    pack_id: str,
) -> Path:
    """Deterministic edition output directory without loading the catalog."""
    return (
        franchise_dir.resolve() / "series" / series_id / "books" / book_id / "editions" / pack_id
    )
