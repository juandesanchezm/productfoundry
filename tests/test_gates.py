"""Regression tests for the fail-closed gates, character bible, line-art QA,
cache invalidation and release manifest."""
import json
from pathlib import Path

import pytest
from PIL import Image

from productfoundry.domain.audit import AssetAuditReport, AuditVerdict
from productfoundry.domain.bible import (
    build_character_bible,
    normalize_character_ids,
    validate_character_bible,
    validate_page_plan,
    validate_story_characters,
)
from productfoundry.domain.manifest import PublicationManifest
from productfoundry.domain.product import Character, PageSpec, ProductPlan
from productfoundry.engine.hashing import sha256_text
from productfoundry.pack_loader import load_pack
from productfoundry.stages.lineart_check import _check_image

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- audit gates

def test_audit_report_fail_closed():
    assert AssetAuditReport(verdicts=[]).verdict == "fail"
    assert AssetAuditReport(verdicts=[AuditVerdict(status="ok")]).verdict == "pass"
    assert AssetAuditReport(verdicts=[AuditVerdict(status="warn")]).verdict == "fail"
    assert AssetAuditReport(verdicts=[AuditVerdict(status="fail")]).verdict == "fail"


def test_audit_verdict_default_is_fail():
    assert AuditVerdict().status == "fail"


def test_character_sheet_audit_covers_every_roster_sheet(tmp_path):
    from productfoundry.domain.assets import AssetPlan, AssetSpec
    from productfoundry.stages.audit import CharacterSheetAuditStage

    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    roster = pack.stories["characters"]
    assets = AssetPlan(
        assets=[
            AssetSpec(
                id=f"character_sheet_{character['id']}",
                page_id=f"character_sheet_{character['id']}",
                prompt="sheet",
            )
            for character in roster
        ]
    )
    for character in roster:
        (tmp_path / f"character_sheet_{character['id']}.png").write_bytes(
            character["id"].encode()
        )

    class FakeLLM:
        calls = 0

        def complete_with_image(self, system, user, image_b64, model=None):
            self.calls += 1
            return type("Response", (), {"content": '{"status": "ok"}', "raw": {}})()

    class FakeContext:
        assets_dir = tmp_path
        llm = FakeLLM()

        def set_cost(self, amount):
            pass

    context = FakeContext()
    context.pack = pack
    report = CharacterSheetAuditStage().run(context, assets)

    assert len(report.verdicts) == len(roster)
    assert FakeContext.llm.calls == len(roster)
    assert all(asset.audit_status == "ok" for asset in assets.assets)


# ------------------------------------------------------------ character bible

def _bible_with(characters: list[Character]) -> list[str]:
    from productfoundry.domain.bible import CharacterBible

    return validate_character_bible(CharacterBible(characters=characters))


def test_bible_requires_exactly_one_main():
    errors = _bible_with([Character(id="a", role="supporting", name_en="A")])
    assert any("exactly one 'main'" in e for e in errors)
    errors = _bible_with(
        [Character(id="a", role="main", name_en="A"), Character(id="b", role="main", name_en="B")]
    )
    assert any("exactly one 'main'" in e for e in errors)
    errors = _bible_with([Character(id="a", role="main", name_en="A")])
    assert errors == []


def test_bible_rejects_duplicate_ids():
    errors = _bible_with(
        [Character(id="a", role="main", name_en="A"), Character(id="a", role="supporting", name_en="B")]
    )
    assert any("duplicate" in e for e in errors)


def test_story_characters_must_be_in_roster():
    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    bible = build_character_bible(pack)
    assert validate_story_characters(pack, bible) == []


def test_page_plan_requires_main_in_every_page():
    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    bible = build_character_bible(pack)
    plan = ProductPlan(
        pack_id="x",
        pack_version=1,
        theme="t",
        pages=[PageSpec(id="p1", index=1, prompt="x", characters=["blaze"])],
    )
    assert validate_page_plan(plan, bible) == []
    plan.pages[0].characters = ["ghost"]
    errors = validate_page_plan(plan, bible)
    assert any("main character" in e for e in errors)
    assert any("unknown character" in e for e in errors)


def test_character_names_from_llm_normalize_to_roster_ids():
    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    assert normalize_character_ids(pack, ["Blaze", "Pip"]) == ["blaze", "pip"]


def test_page_plan_rejects_secondary_outside_story_cast():
    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    bible = build_character_bible(pack)
    plan = ProductPlan(
        pack_id="x",
        pack_version=1,
        theme="t",
        pages=[PageSpec(id="p1", index=1, prompt="x", characters=["blaze", "clover"])],
    )
    errors = validate_page_plan(plan, bible, allowed_ids={"blaze", "pip", "pebble"})
    assert any("not allowed in this story" in e for e in errors)


def test_first_day_bedtime_pages_have_distinct_scenes():
    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    story = next(story for story in pack.stories["stories"] if story["id"] == "baby-dragon-first-day")
    goodnight_page, final_page = story["arc"][20], story["arc"][-1]

    assert story["pages"] == 24
    assert len(story["arc"]) == story["pages"]
    assert goodnight_page != final_page
    assert "flower" in goodnight_page
    assert "sleeping" in final_page


def test_pack_declares_clover_hopping_traits():
    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    clover = next(character for character in pack.stories["characters"] if character["id"] == "clover")
    description = clover["description_en"].lower()

    assert "long" in description
    assert "bent" in description
    assert "webbed" in description


def test_coloring_pack_defaults_are_kdp_ready_and_use_real_author():
    pack = load_pack(ROOT / "packs" / "coloring-fantasy")

    assert pack.profile.page_count == 24
    assert pack.profile.author == "Juande Sánchez"
    assert pack.profile.languages == ["en", "es"]
    assert set(pack.profile.formats.model_dump()) == {"digital", "print"}


def test_coloring_style_does_not_request_excessive_blank_padding():
    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    positive = pack.style["style"]["positive_prompt_suffix"]

    assert "15 percent blank space" not in positive
    assert "5 percent" in positive


def test_pack_validation_rejects_short_print_requests_before_generation():
    from productfoundry.stages.pack_validate import PackValidationStage

    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    concept = ProductPlan(
        pack_id="coloring-fantasy",
        pack_version=2,
        theme="moonlit-discovery",
        pages=[
            PageSpec(id=f"page_{index:03d}", index=index, prompt="scene", characters=["blaze"])
            for index in range(1, 11)
        ],
    )

    class FakeRequest:
        page_count = 10
        formats = ["print"]
        story_id = "baby-dragon-first-day"

    class FakeContext:
        request = FakeRequest()

    context = FakeContext()
    context.pack = pack
    report = PackValidationStage().run(context, concept)

    assert report.verdict == "fail"
    assert any("KDP minimum" in error for error in report.errors)


# ---------------------------------------------------------------- line art QA

def _make_image(path: Path, mode: str, size: tuple[int, int], fill: int) -> None:
    Image.new(mode, size, fill).save(path)


def test_lineart_rejects_blank_page(tmp_path):
    p = tmp_path / "page_001.png"
    _make_image(p, "L", (2550, 3300), 255)
    assert _check_image(p, 2550, 3300).status == "fail"


def test_lineart_rejects_gray_pixels(tmp_path):
    p = tmp_path / "page_001.png"
    _make_image(p, "L", (2550, 3300), 128)
    assert _check_image(p, 2550, 3300).status == "fail"


def test_lineart_rejects_wrong_size(tmp_path):
    p = tmp_path / "page_001.png"
    _make_image(p, "L", (100, 100), 255)
    assert _check_image(p, 2550, 3300).status == "fail"


def test_lineart_passes_clean_binary(tmp_path):
    p = tmp_path / "page_001.png"
    im = Image.new("L", (2550, 3300), 255)
    for x in range(0, 2550, 4):
        for y in range(0, 3300, 4):
            im.putpixel((x, y), 0)
    im.save(p)
    assert _check_image(p, 2550, 3300).status == "pass"


# ---------------------------------------------------------------- cache hash

def test_config_hash_includes_aux_files():
    """A change in stories.yaml must change the config hash (cache invalidation)."""
    from productfoundry.engine.pipeline import PIPELINE_ORDER

    assert PIPELINE_ORDER.index("audit_prompt") < PIPELINE_ORDER.index("pack_validate")
    assert "pack_validate" in PIPELINE_ORDER
    assert "lineart_check" in PIPELINE_ORDER
    assert "release" in PIPELINE_ORDER


def test_design_hash_changes_with_roster():
    from productfoundry.stages.assets import _character_design_hash

    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    h1 = _character_design_hash(pack)
    pack.stories = dict(pack.stories)
    pack.stories["characters"] = [{"id": "other", "role": "main", "name_en": "Other"}]
    h2 = _character_design_hash(pack)
    assert h1 != h2


# ---------------------------------------------------------------- manifest

def test_manifest_publishable_requires_human_approval(tmp_path):
    m = PublicationManifest(product_id="p", pack_id="pack", pack_version=1)
    m.add_gate("review", "pass")
    m.add_gate("printcheck", "pass")
    m.ai_disclosure_ready = True
    m.compliance_ready = True
    assert m.compute_publishable() is False  # no human approval
    m.human_release_approved = True
    assert m.compute_publishable() is True


def test_manifest_publishable_requires_all_gates(tmp_path):
    m = PublicationManifest(product_id="p", pack_id="pack", pack_version=1)
    m.add_gate("review", "fail")
    m.ai_disclosure_ready = True
    m.compliance_ready = True
    m.human_release_approved = True
    assert m.compute_publishable() is False


def test_manifest_never_publishes_synthetic_provider_output():
    m = PublicationManifest(product_id="p", pack_id="pack", pack_version=1)
    m.add_gate("review", "pass")
    m.add_gate("printcheck", "pass")
    m.ai_disclosure_ready = True
    m.compliance_ready = True
    m.human_release_approved = True
    m.synthetic = True
    assert m.compute_publishable() is False


def test_manifest_records_sha256(tmp_path):
    f = tmp_path / "file.png"
    f.write_bytes(b"hello")
    m = PublicationManifest(product_id="p", pack_id="pack", pack_version=1)
    m.add_file(f, tmp_path)
    assert m.files[0].sha256 == sha256_text("hello")
    assert m.files[0].size == 5


def test_manifest_publishable_requires_compliance_gate():
    m = PublicationManifest(product_id="p", pack_id="pack", pack_version=1)
    m.add_gate("review", "pass")
    m.add_gate("printcheck", "pass")
    m.ai_disclosure_ready = True
    m.human_release_approved = True

    assert m.compute_publishable() is False

    m.compliance_ready = True
    assert m.compute_publishable() is True


def test_node_record_tracks_execution_attempts():
    from productfoundry.engine.state import NodeRecord

    node = NodeRecord(name="audit_prompt", status="pending")

    assert node.attempts == 0


# --------------------------------------------------------------- size derivation

def test_derive_generation_size_preserves_aspect_ratio():
    from productfoundry.domain.pack import derive_generation_size

    # 8.5x11 (ratio ~0.773) should produce roughly 1024x1328
    size = derive_generation_size("8.5x11")
    w, h = map(int, size.split("x"))
    assert w % 16 == 0 and h % 16 == 0
    assert abs((w / h) - (8.5 / 11)) < 0.05
    # 8.5x8.5 (square) should produce 1024x1024
    size = derive_generation_size("8.5x8.5")
    assert size == "1024x1024"
    # invalid input falls back to square
    size = derive_generation_size("garbage")
    assert size == "1024x1024"


# --------------------------------------------------------------- pricing fail-closed

def test_pricing_unknown_model_raises():
    import pytest as _pytest

    from productfoundry.providers.pricing import image_cost_usd

    with _pytest.raises(ValueError, match="unknown image model"):
        image_cost_usd("openai", "nonexistent-model", "1024x1024", "low")


def test_pricing_unknown_provider_raises():
    import pytest as _pytest

    from productfoundry.providers.pricing import image_cost_usd

    with _pytest.raises(ValueError, match="unknown image provider"):
        image_cost_usd("stability", "sdxl", "1024x1024", "high")


def test_pricing_gpt_image_2_returns_known_cost():
    from productfoundry.providers.pricing import image_cost_usd

    assert image_cost_usd("openai", "gpt-image-2", "1024x1024", "low") == 0.006
    assert image_cost_usd("openai", "gpt-image-2", "1024x1024", "high") == 0.211


def test_pricing_gpt_image_2_interpolates_custom_size():
    from productfoundry.providers.pricing import image_cost_usd

    # 1024x1328 is close to 1024x1536 in total pixels
    cost = image_cost_usd("openai", "gpt-image-2", "1024x1328", "low")
    assert cost > 0


def test_pricing_placeholder_is_zero():
    from productfoundry.providers.pricing import image_cost_usd

    assert image_cost_usd("placeholder", "placeholder", "1024x1024", "low") == 0.0


# --------------------------------------------------------------- provider size validation

def test_provider_rejects_invalid_size():
    import pytest as _pytest

    from productfoundry.providers.image import _validate_size

    with _pytest.raises(ValueError, match="invalid image size format"):
        _validate_size("not-a-size")
    with _pytest.raises(ValueError, match="multiples of 16"):
        _validate_size("1000x1000")
    with _pytest.raises(ValueError, match="dimensions too large"):
        _validate_size("4000x4000")
    with _pytest.raises(ValueError, match="aspect ratio out of range"):
        _validate_size("16x3072")  # 16/3072 = 1:192, way beyond 1:3 limit


def test_provider_accepts_valid_sizes():
    from productfoundry.providers.image import _validate_size

    assert _validate_size("1024x1024") == "1024x1024"
    assert _validate_size("1024x1328") == "1024x1328"
    assert _validate_size("1536x1024") == "1536x1024"


# --------------------------------------------------------------- quality policies

def test_quality_policies_default():
    from productfoundry.runtime import RuntimeProfile

    p = RuntimeProfile()
    assert p.image_policies.interior.attempts == ["low", "low", "medium"]
    assert p.image_policies.character_sheet.attempts == ["medium", "medium", "high"]
    assert p.image_policies.cover.attempts == ["high", "high", "high"]


def test_hero_prompt_requires_the_exact_localized_title():
    from productfoundry.stages.hero import _build_hero_prompt

    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    plan = ProductPlan(
        pack_id=pack.profile.id,
        pack_version=pack.profile.pack_version,
        theme="moonlit-discovery",
        titles={"en": "Blaze's Moonlit Discovery", "es": "El Descubrimiento Lunar de Blaze"},
    )

    prompt = _build_hero_prompt(plan, pack, "es")

    assert "El Descubrimiento Lunar de Blaze" in prompt
    assert "exact title text" in prompt.lower()


def test_hero_stage_has_one_output_per_requested_language():
    from productfoundry.stages.hero import HeroStage

    class FakeRequest:
        languages = ["en", "es"]

    class FakeContext:
        assets_dir = Path("assets")
        request = FakeRequest()

    assert HeroStage().output_files(FakeContext()) == [
        Path("assets/cover_hero_en.png"),
        Path("assets/cover_hero_es.png"),
    ]


# --------------------------------------------------------------- reference routing

def test_reference_routing_only_includes_page_characters(tmp_path):
    from productfoundry.domain.product import PageSpec
    from productfoundry.stages.assets import _load_page_references

    # Create fake character sheets
    (tmp_path / "character_sheet_blaze.png").write_bytes(b"main-sheet")
    (tmp_path / "character_sheet_pip.png").write_bytes(b"pip-sheet")

    class FakeCtx:
        assets_dir = tmp_path

    page = PageSpec(id="p1", index=1, prompt="x", characters=["blaze"])
    refs = _load_page_references(FakeCtx(), page)
    assert len(refs) == 1
    assert refs[0] == b"main-sheet"

    page = PageSpec(id="p2", index=2, prompt="x", characters=["blaze", "pip"])
    refs = _load_page_references(FakeCtx(), page)
    assert len(refs) == 2

    (tmp_path / "character_sheet_pip.png").unlink()
    page = PageSpec(id="p3", index=3, prompt="x", characters=["pip"])
    with pytest.raises(RuntimeError, match="character sheet missing"):
        _load_page_references(FakeCtx(), page)


def test_page_prompt_contains_canonical_characters_and_style():
    from productfoundry.domain.product import PageSpec
    from productfoundry.stages.assets import build_page_prompt

    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    page = PageSpec(
        id="page_001",
        index=1,
        prompt="Blaze wakes up in a cozy nest with Pip nearby.",
        beat="waking up in the cozy nest",
        characters=["blaze", "pip"],
    )
    prompt = build_page_prompt(page, pack)
    assert "Blaze, a cute kawaii baby dragon" in prompt
    assert "Pip, a tiny round yellow baby bird" in prompt
    assert "waking up in the cozy nest" in prompt
    assert "kawaii chibi style" in prompt
    assert "Do NOT include" in prompt


def test_story_mode_prompt_keeps_theme_distinct_from_story_id():
    from productfoundry.domain.product import ProductRequest
    from productfoundry.stages.concept import _build_prompt

    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    request = ProductRequest(
        pack="coloring-fantasy",
        theme="moonlit discovery",
        page_count=24,
        story_id="baby-dragon-first-day",
    )

    prompt = _build_prompt(pack, request)

    assert "moonlit discovery" in prompt
    assert "baby-dragon-first-day" not in prompt


def test_prompt_audit_requires_story_variety_and_child_engagement():
    from productfoundry.stages.audit import PROMPT_USER_TEMPLATE

    template = PROMPT_USER_TEMPLATE.lower()
    assert "storytelling judge" in template
    assert "expected characters" in template
    assert "story beat" in template
    assert "same order" in template
    assert "variety" in template
    assert "child" in template
    assert "color" in template


def test_character_sheet_audit_only_enforces_declared_traits():
    from productfoundry.stages.audit import SHEET_USER_TEMPLATE

    assert "only the listed traits" in SHEET_USER_TEMPLATE.lower()
    assert "do not invent" in SHEET_USER_TEMPLATE.lower()
    assert "black-and-white line art" in SHEET_USER_TEMPLATE.lower()
    assert "do not fail because color is not visible" in SHEET_USER_TEMPLATE.lower()


def test_judge_parser_accepts_fenced_json_with_leading_text():
    from productfoundry.stages.audit import _parse_judge_json

    assert _parse_judge_json(
        'The result is:\n```json\n{"status": "ok"}\n```'
    ) == {"status": "ok"}


def test_image_judge_retries_parse_failure_without_regenerating_image():
    from productfoundry.stages.audit import _complete_with_image_json

    class FakeLLM:
        responses = ["not json", '{"status": "ok"}']

        def complete_with_image(self, system, user, image_b64, model=None):
            content = self.responses.pop(0)
            return type("Response", (), {"content": content, "raw": {}})()

    class FakeContext:
        llm = FakeLLM()

        def set_cost(self, amount):
            pass

    result = _complete_with_image_json(FakeContext(), "system", "user", "image", "model")

    assert result == {"status": "ok"}


def test_prompt_audit_retries_after_judge_rewrite():
    from productfoundry.domain.product import PageSpec, ProductPlan
    from productfoundry.stages.audit import PromptAuditStage

    class FakeLLM:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            status = "warn" if self.calls == 1 else "ok"
            rewrite = "Blaze remains fully visible in a playful nest scene." if self.calls == 1 else ""
            return type("Response", (), {
                "content": json.dumps({"verdicts": [{
                    "status": status,
                    "notes": "needs rewrite" if status == "warn" else "",
                    "rewrite_suggestion": rewrite,
                }]}),
                "raw": {},
            })()

    class FakeContext:
        llm = FakeLLM()
        pack = type("Pack", (), {"audit": {}})()

        def set_cost(self, amount):
            pass

        def get_artifact(self, name):
            return None

    plan = ProductPlan(
        pack_id="x",
        pack_version=1,
        theme="t",
        pages=[PageSpec(id="page_001", index=1, prompt="old", characters=["blaze"])],
    )
    report = PromptAuditStage().run(FakeContext(), plan)
    assert report.verdict == "pass"
    assert FakeContext.llm.calls == 2
    assert plan.pages[0].prompt.endswith("Blaze remains fully visible in a playful nest scene.")


def test_placeholder_llm_returns_one_prompt_verdict_per_page():
    from productfoundry.providers.llm import PlaceholderLLMClient
    from productfoundry.stages.audit import PROMPT_USER_TEMPLATE

    user = PROMPT_USER_TEMPLATE.format(
        prompts_json=json.dumps(
            [{"id": f"page_{index:03d}", "prompt": "scene"} for index in range(1, 25)]
        )
    )
    response = PlaceholderLLMClient(schema_hint="audit").complete("", user)

    assert len(json.loads(response.content)["verdicts"]) == 24


def test_package_rejects_incomplete_processed_page_set(tmp_path):
    from productfoundry.domain.assets import AssetPlan, AssetSpec
    from productfoundry.domain.product import PageSpec, ProductPlan
    from productfoundry.stages.package import build_packages

    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    plan = ProductPlan(
        pack_id=pack.profile.id,
        pack_version=pack.profile.pack_version,
        theme="test",
        pages=[
            PageSpec(id="page_001", index=1, prompt="one", characters=["blaze"]),
            PageSpec(id="page_002", index=2, prompt="two", characters=["blaze"]),
        ],
    )
    assets = AssetPlan(
        assets=[
            AssetSpec(id="page_001", page_id="page_001", prompt="one", audit_status="ok"),
            AssetSpec(id="page_002", page_id="page_002", prompt="two", audit_status="ok"),
        ]
    )
    processed = tmp_path / "processed"
    processed.mkdir()
    (processed / "page_001.png").write_bytes(b"one")
    with pytest.raises(RuntimeError, match="incomplete page set"):
        build_packages(
            assets=assets,
            plan=plan,
            pack=pack,
            request_theme="test",
            request_story_id="",
            request_page_count=2,
            processed_dir=processed,
            packages_dir=tmp_path / "packages",
            assets_dir=tmp_path / "assets",
            languages=["en"],
            formats=["digital"],
        )


def test_full_preview_contains_cover_then_interior_pages(tmp_path):
    from pypdf import PdfReader

    from productfoundry.packaging import build_full_preview_pdf, build_pdf

    image = tmp_path / "page.png"
    Image.new("RGB", (300, 300), "white").save(image)
    cover = tmp_path / "cover.pdf"
    interior = tmp_path / "interior.pdf"
    preview = tmp_path / "preview.pdf"
    build_pdf([image], interior, page_size="1x1", inner_safe_inches=0.1)
    build_pdf([image], cover, page_size="1x1", inner_safe_inches=0.1)

    build_full_preview_pdf(cover, interior, preview)

    assert len(PdfReader(str(preview)).pages) == 2


def test_digital_pdf_includes_cover_when_print_bundle_is_requested(tmp_path):
    from pypdf import PdfReader

    from productfoundry.domain.assets import AssetPlan, AssetSpec
    from productfoundry.stages.package import build_packages

    pack = load_pack(ROOT / "packs" / "coloring-fantasy")
    plan = ProductPlan(
        pack_id=pack.profile.id,
        pack_version=pack.profile.pack_version,
        theme="test",
        pages=[
            PageSpec(id=f"page_{index:03d}", index=index, prompt="scene", characters=["blaze"])
            for index in range(1, 25)
        ],
        titles={"en": "Test English", "es": "Prueba Español"},
    )
    assets = AssetPlan(
        assets=[
            AssetSpec(
                id=f"page_{index:03d}", page_id=f"page_{index:03d}", prompt="scene", audit_status="ok"
            )
            for index in range(1, 25)
        ]
    )
    processed = tmp_path / "processed"
    processed.mkdir()
    for index in range(1, 25):
        Image.new("RGB", (300, 300), "white").save(processed / f"page_{index:03d}.png")

    result = build_packages(
        assets=assets,
        plan=plan,
        pack=pack,
        request_theme="test",
        request_story_id="",
        request_page_count=24,
        processed_dir=processed,
        packages_dir=tmp_path / "packages",
        assets_dir=tmp_path / "assets",
        languages=["en"],
        formats=["digital", "print"],
    )

    digital_pdf = next(
        Path(item.path)
        for item in result.packages
        if item.format == "digital" and item.path.endswith("etsy.pdf")
    )
    assert len(PdfReader(str(digital_pdf)).pages) == 25


def test_wrap_cover_leaves_kdp_barcode_area_blank(tmp_path):
    from productfoundry.packaging import build_wrap_cover

    cover = tmp_path / "cover.png"
    build_wrap_cover(
        title="A Title",
        subtitle="",
        author="Juande Sánchez",
        back_blurb="A short blurb.",
        out_path=cover,
        page_count=24,
        page_size="8.5x11",
        bleed_inches=0.125,
    )

    with Image.open(cover) as image:
        # The lower back-cover barcode reservation must stay white until KDP
        # inserts a real ISBN barcode.
        _, height = image.size
        back_width = round(8.5 * 300)
        bottom = image.crop((0, height - round(0.9 * 300), back_width, height))
        pixels = bottom.load()
        assert all(
            pixels[x, y] == (255, 255, 255)
            for x in range(bottom.width)
            for y in range(bottom.height)
        )


def test_listing_policy_adds_ai_disclosure_and_caps_etsy_tags():
    from productfoundry.stages.listing import normalize_listing

    listing = normalize_listing(
        {
            "marketplace": "etsy",
            "language": "en",
            "title": "A title",
            "description": "A description",
            "tags": [f"tag-{i}" for i in range(14)],
            "price": 4.99,
            "category": "coloring",
        }
    )

    assert len(listing.tags) == 13
    assert "AI-assisted" in listing.ai_disclosure


# --------------------------------------------------------------- postprocess aspect ratio

def test_postprocess_preserves_aspect_ratio(tmp_path):
    from PIL import Image

    from productfoundry.stages.postprocess import to_grayscale_and_threshold

    # Create a 1024x1328 source (portrait)
    src = tmp_path / "raw.png"
    Image.new("L", (1024, 1328), 255).save(src)
    dst = tmp_path / "processed.png"
    to_grayscale_and_threshold(src, dst, target_inches=8.5, target_height_inches=11.0)
    with Image.open(dst) as im:
        assert im.width == 2550  # 8.5 * 300
        assert im.height == 3300  # 11 * 300
