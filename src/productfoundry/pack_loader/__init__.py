"""Pack loader — reads YAML pack packs from disk and validates them."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from productfoundry.domain.pack import PackProfile


class PackError(Exception):
    pass


@dataclass
class Pack:
    profile: PackProfile
    root: Path = field(default_factory=Path)
    character_root: Path = field(default_factory=Path)
    style: dict = field(default_factory=dict)
    themes: dict = field(default_factory=dict)
    packaging: dict = field(default_factory=dict)
    listing: dict = field(default_factory=dict)
    quality: dict = field(default_factory=dict)
    audit: dict = field(default_factory=dict)
    stories: dict = field(default_factory=dict)
    compliance: dict = field(default_factory=dict)
    series: dict = field(default_factory=dict)
    palettes: dict = field(default_factory=dict)  # franchise-only: {char_id: {"en": ..., "es": ...}}


def _read_yaml(path: Path) -> dict:
    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError as e:
        raise PackError(f"missing {path}: {e}") from e
    except yaml.YAMLError as e:
        raise PackError(f"invalid yaml {path}: {e}") from e
    if not isinstance(data, dict):
        raise PackError(f"invalid yaml {path}: expected a mapping, got {type(data).__name__}")
    return data


def load_pack(pack_dir: Path) -> Pack:
    base = _read_yaml(pack_dir / "pack.yaml")
    try:
        profile = PackProfile.model_validate(base)
    except Exception as e:
        raise PackError(f"invalid pack profile: {e}") from e

    aux_files = (
        "style", "themes", "packaging", "listing", "quality", "audit", "stories", "compliance", "series"
    )
    aux: dict[str, dict] = {}
    for name in aux_files:
        path = pack_dir / f"{name}.yaml"
        if path.exists():
            aux[name] = _read_yaml(path)

    return Pack(profile=profile, root=pack_dir, **aux)
