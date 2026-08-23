from types import SimpleNamespace

from productfoundry.series import (
    canonical_character_reference,
    character_definition_hash,
    validate_series_contract,
)


def _pack(tmp_path, characters, contracts):
    for character_id, contract in contracts.items():
        reference = contract.get("reference_image")
        if reference:
            path = tmp_path / reference
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"reference-{character_id}".encode())
    return SimpleNamespace(
        root=tmp_path,
        stories={"characters": characters},
        series={
            "series": {
                "id": "test-series",
                "version": 1,
                "characters": contracts,
            }
        },
    )


def _character(character_id, description="stable design"):
    return {
        "id": character_id,
        "role": "main" if character_id == "main" else "supporting",
        "name_en": character_id.title(),
        "name_es": character_id.title(),
        "archetype_en": "friendly character",
        "archetype_es": "personaje amable",
        "description_en": description,
        "description_es": description,
    }


def test_series_contract_accepts_registered_characters_with_references(tmp_path):
    characters = [_character("main"), _character("friend")]
    contracts = {
        "main": {"reference_image": "characters/main.png"},
        "friend": {"reference_image": "characters/friend.png"},
    }
    for character in characters:
        contracts[character["id"]]["definition_hash"] = character_definition_hash(character)

    pack = _pack(tmp_path, characters, contracts)

    assert validate_series_contract(pack) == []
    assert canonical_character_reference(pack, "main") == tmp_path / "characters/main.png"


def test_series_contract_rejects_changed_existing_character(tmp_path):
    characters = [_character("main", description="changed design")]
    contracts = {
        "main": {
            "reference_image": "characters/main.png",
            "definition_hash": "locked-to-another-definition",
        }
    }

    errors = validate_series_contract(_pack(tmp_path, characters, contracts))

    assert any("main" in error and "definition hash" in error for error in errors)


def test_series_contract_rejects_missing_reference(tmp_path):
    pack = SimpleNamespace(
        root=tmp_path,
        stories={"characters": [_character("main")]},
        series={"series": {"id": "test-series", "version": 1, "characters": {"main": {}}}},
    )

    errors = validate_series_contract(pack)

    assert any("reference image" in error for error in errors)


def test_character_sheet_stage_does_not_copy_canonical_references(tmp_path):
    from productfoundry.stages.character_sheet import CharacterSheetStage

    characters = [_character("main")]
    contracts = {"main": {"reference_image": "characters/main.png"}}
    contracts["main"]["definition_hash"] = character_definition_hash(characters[0])
    pack = _pack(tmp_path, characters, contracts)
    assets_dir = tmp_path / "project-assets"

    class FailingProvider:
        def generate(self, request):
            raise AssertionError("canonical character must not be regenerated")

    ctx = SimpleNamespace(
        pack=SimpleNamespace(**pack.__dict__, profile=SimpleNamespace(image_size="1024x1328")),
        assets_dir=assets_dir,
        runtime=SimpleNamespace(
            image_policies=SimpleNamespace(character_sheet=SimpleNamespace(attempts=["medium"]))
        ),
        image_provider=FailingProvider(),
        set_cost=lambda amount: (_ for _ in ()).throw(AssertionError("canonical copy has no cost")),
    )

    CharacterSheetStage().run(ctx)

    # Canonical characters are the single source of truth: nothing is copied
    # into the edition's assets directory.
    assert not (assets_dir / "character_sheet_main.png").exists()
    assert not (assets_dir / "character_sheet.png").exists()
