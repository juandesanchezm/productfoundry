from types import SimpleNamespace

from PIL import Image, ImageStat

from productfoundry.packaging import ImageOps_contain, build_wrap_cover, localized_age_label
from productfoundry.stages.story_helpers import localized_series_name


def test_image_ops_contain_preserves_full_source_inside_target():
    source = Image.new("RGB", (4, 8), "white")
    source.putpixel((0, 0), (255, 0, 0))
    source.putpixel((3, 7), (0, 0, 255))

    result = ImageOps_contain(source, 12, 12)

    assert result.size == (12, 12)
    assert result.getpixel((0, 0)) == (255, 255, 255)
    pixels = [result.getpixel((x, y)) for y in range(result.height) for x in range(result.width)]
    assert any(red > green + 30 for red, green, _ in pixels)
    assert any(blue > green + 30 for _, green, blue in pixels)


def test_wrap_cover_leaves_spine_blank(tmp_path):
    output = tmp_path / "cover.png"

    build_wrap_cover(
        title="A Title",
        author="An Author",
        back_blurb="A short description.",
        out_path=output,
        page_count=24,
        page_size="8.5x11",
    )

    with Image.open(output) as cover:
        bleed = round(0.125 * 300)
        trim_width = round(8.5 * 300)
        spine_width = round(24 * 0.002252 * 300)
        spine = cover.crop(
            (bleed + trim_width, bleed, bleed + trim_width + spine_width, bleed + (11 * 300))
        )
        assert max(ImageStat.Stat(spine).extrema[0]) == 255


def test_age_label_is_localized():
    assert localized_age_label("es", "3-8") == "Edad 3-8"
    assert localized_age_label("en", "3-8") == "Ages 3-8"


def test_series_name_is_localized():
    pack = SimpleNamespace(profile=SimpleNamespace(series_name={"es": "Las Aventuras de Cocholate", "en": "The Adventures of Cocholate"}))

    assert localized_series_name(pack, "es") == "Las Aventuras de Cocholate"
    assert localized_series_name(pack, "en") == "The Adventures of Cocholate"


def test_series_name_falls_back_to_plain_string():
    pack = SimpleNamespace(profile=SimpleNamespace(series_name="Blaze & Friends"))

    assert localized_series_name(pack, "es") == "Blaze & Friends"
    assert localized_series_name(pack, "en") == "Blaze & Friends"


def test_wrap_cover_back_uses_background_image_when_provided(tmp_path):
    background = tmp_path / "back.png"
    Image.new("RGB", (300, 300), "lightblue").save(background)
    output = tmp_path / "cover.png"

    build_wrap_cover(
        title="A Title",
        author="An Author",
        back_blurb="A short description.",
        out_path=output,
        page_count=24,
        page_size="8.5x11",
        back_image_path=background,
        thumbnail_paths=[],
    )

    with Image.open(output) as cover:
        bleed = round(0.125 * 300)
        back = cover.crop((bleed, bleed, bleed + round(8.5 * 300), bleed + (4 * 300)))
        # The upper band of the back cover has no blurb overlay: it must show
        # the model-generated background, i.e. non-white pixels.
        assert min(ImageStat.Stat(back).extrema[0]) < 250


def test_wrap_cover_front_overlay_uses_translucent_panel_not_opaque_box(tmp_path):
    # A red hero image: the overlay panel is translucent (alpha ~150), so the
    # red artwork must remain visible through it — an opaque white box would
    # make the panel band pure white.
    hero = tmp_path / "hero.png"
    Image.new("RGB", (600, 600), (220, 60, 60)).save(hero)
    output = tmp_path / "cover.png"

    build_wrap_cover(
        title="A Long Book Title",
        author="An Author",
        back_blurb="",
        out_path=output,
        page_count=24,
        page_size="8.5x11",
        hero_image_path=hero,
        back_image_path=None,
        thumbnail_paths=[],
        title_in_artwork=False,
    )

    with Image.open(output) as cover:
        bleed = round(0.125 * 300)
        trim_width = round(8.5 * 300)
        spine_width = round(24 * 0.002252 * 300)
        # Panel band (0.35in..1.35in of the front trim)
        front = cover.crop(
            (
                bleed + trim_width + spine_width,
                bleed + round(0.35 * 300),
                bleed + 2 * trim_width + spine_width,
                bleed + round(1.35 * 300),
            )
        )
        stat = ImageStat.Stat(front)
        # Red tint must remain through the translucent panel (not pure white)
        assert 80 < stat.mean[0] < 240
        assert stat.mean[1] < 160  # green stays low (red base)


def test_wrap_cover_back_renders_two_by_three_thumbnail_grid(tmp_path):
    # 6 thumbnails → grid 3 rows x 2 columns, centered, filling the back
    thumbs = []
    for i, c in enumerate((30, 70, 110, 150, 190, 230)):
        p = tmp_path / f"thumb_{i}.png"
        Image.new("RGB", (60, 60), (c, c, c)).save(p)
        thumbs.append(p)
    output = tmp_path / "cover.png"

    build_wrap_cover(
        title="A Title",
        author="An Author",
        back_blurb="",
        out_path=output,
        page_count=24,
        page_size="8.5x11",
        thumbnail_paths=thumbs,
    )

    with Image.open(output) as cover:
        bleed = round(0.125 * 300)
        trim_width = round(8.5 * 300)
        # Grid band: from the top of the back cover to the barcode zone (1.5in)
        band = cover.crop(
            (bleed, bleed, bleed + trim_width, bleed + round((11 - 1.5) * 300))
        )
        colors = set()
        for y in range(0, band.height, 10):
            for x in range(0, band.width, 10):
                px = band.getpixel((x, y))
                # Skip white background and gray outline (200)
                if px[0] == px[1] == px[2] and 0 < px[0] < 200 and px[0] % 10 == 0:
                    colors.add(px[0])
        assert len(colors) >= 5  # at least 5 of the 6 shades visible
        # The grid must cover a large vertical portion (3 rows ≈ 4.8in of the 9.5in band)
        nonwhite = 0
        for y in range(band.height):
            for x in range(0, band.width, 20):
                px = band.getpixel((x, y))
                if px[0] != 255 or px[1] != 255 or px[2] != 255:
                    nonwhite += 1
        assert nonwhite > 2000


def test_wrap_cover_back_has_no_blurb_text_or_barcode_box(tmp_path):
    output = tmp_path / "cover.png"

    build_wrap_cover(
        title="A Title",
        author="An Author",
        back_blurb="This blurb must NOT be rendered: the back cover stays clean for KDP.",
        out_path=output,
        page_count=24,
        page_size="8.5x11",
        thumbnail_paths=[],
    )

    with Image.open(output) as cover:
        bleed = round(0.125 * 300)
        trim_width = round(8.5 * 300)
        trim_height = 11 * 300
        # The middle band of the back cover (former blurb zone) must be empty
        back_mid = cover.crop(
            (bleed, bleed + round(0.4 * trim_height), bleed + trim_width, bleed + round(0.85 * trim_height))
        )
        assert max(ImageStat.Stat(back_mid).extrema[0]) == 255
        # The lower band (former barcode box) must also be plain white
        back_bottom = cover.crop(
            (bleed, bleed + round(0.85 * trim_height), bleed + trim_width, bleed + trim_height)
        )
        assert max(ImageStat.Stat(back_bottom).extrema[0]) == 255
