"""Deterministic packaging — build digital PDFs, print-ready PDFs (with bleed), wrap covers, and ZIPs."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import img2pdf
from PIL import Image, ImageDraw, ImageFont


def _inches_to_pixels(inches: float, dpi: int = 300) -> int:
    return round(inches * dpi)


def build_pdf(
    image_paths: list[Path],
    out_path: Path,
    page_size: str = "8.5x11",
    bleed: float = 0.0,
    inner_safe_inches: float = 0.25,
) -> Path:
    """Build a PDF from a list of image paths. Pure deterministic, no LLM.

    The PDF page size is `page_size + 2*bleed`. The image is scaled to fit
    a target of `page_size + 2*bleed - 2*inner_safe_inches` (so a safe margin
    of `inner_safe_inches` is kept between the image content and the trim
    edge). The bleed area remains white space inside the PDF; when the
    printer trims the bleed, the safe margin is preserved.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    w_in, h_in = map(float, page_size.split("x"))
    target_w = _inches_to_pixels(w_in + 2 * bleed)
    target_h = _inches_to_pixels(h_in + 2 * bleed)
    # Image content area = trim size minus 2*safe margin on each side.
    # This guarantees no ink is within `inner_safe_inches` of the trim edge.
    content_w = _inches_to_pixels(w_in - 2 * inner_safe_inches)
    content_h = _inches_to_pixels(h_in - 2 * inner_safe_inches)

    resized: list[bytes] = []
    for src in image_paths:
        with Image.open(src) as im:
            im = ImageOps_fit(im, content_w, content_h)
            # Paste the content image onto a full-size white canvas with bleed.
            # The content is centered; the bleed + safe margin stay white.
            canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
            paste_x = (target_w - content_w) // 2
            paste_y = (target_h - content_h) // 2
            canvas.paste(im, (paste_x, paste_y))
            buf = io.BytesIO()
            canvas.save(buf, format="PNG", optimize=True)
            resized.append(buf.getvalue())

    layout = img2pdf.get_layout_fun(
        pagesize=((w_in + 2 * bleed) * 72, (h_in + 2 * bleed) * 72)
    )
    out_path.write_bytes(img2pdf.convert(resized, layout_fun=layout))
    return out_path


def ImageOps_fit(im: Image.Image, w: int, h: int) -> Image.Image:
    """Resize and center-crop image to fit exact w x h."""
    target_ratio = w / h
    src_ratio = im.width / im.height
    if src_ratio > target_ratio:
        # source is wider — crop sides
        new_w = round(im.height * target_ratio)
        left = (im.width - new_w) // 2
        im = im.crop((left, 0, left + new_w, im.height))
    else:
        # source is taller — crop top/bottom
        new_h = round(im.width / target_ratio)
        top = (im.height - new_h) // 2
        im = im.crop((0, top, im.width, top + new_h))
    return im.resize((w, h), Image.LANCZOS)


def ImageOps_contain(
    im: Image.Image,
    w: int,
    h: int,
    background: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Resize the complete source into a fixed canvas without cropping."""
    source = im.convert("RGB")
    scale = min(w / source.width, h / source.height)
    size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
    resized = source.resize(size, Image.LANCZOS)
    canvas = Image.new("RGB", (w, h), background)
    canvas.paste(resized, ((w - resized.width) // 2, (h - resized.height) // 2))
    return canvas


def localized_age_label(language: str, age_range: str) -> str:
    """Return the age badge copy in the requested language."""
    return f"Edad {age_range}" if language.lower() == "es" else f"Ages {age_range}"


def build_cover(
    title: str,
    out_path: Path,
    page_size: str = "8.5x11",
    bg_color: tuple[int, int, int] = (255, 255, 255),
    text_color: tuple[int, int, int] = (20, 20, 20),
    bg_image_path: Path | None = None,
) -> Path:
    """Build a simple cover image (front only). Deterministic, no LLM."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    w_in, h_in = map(float, page_size.split("x"))
    w = _inches_to_pixels(w_in)
    h = _inches_to_pixels(h_in)

    if bg_image_path and bg_image_path.exists():
        with Image.open(bg_image_path) as bg:
            canvas = ImageOps_fit(bg, w, h)
    else:
        canvas = Image.new("RGB", (w, h), bg_color)

    draw = ImageDraw.Draw(canvas)
    title_font = ImageFont.load_default(80)

    if title:
        bbox = draw.textbbox((0, 0), title, font=title_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = 20
        box = [w // 2 - tw // 2 - pad, h // 2 - th // 2 - pad, w // 2 + tw // 2 + pad, h // 2 + th // 2 + pad]
        draw.rectangle(box, fill=(255, 255, 255, 230))
        draw.text((w // 2 - tw // 2, h // 2 - th // 2), title, fill=text_color, font=title_font)

    canvas.save(out_path, "PNG", optimize=True)
    return out_path


def _spine_width_inches(page_count: int, paper: str = "white") -> float:
    """Print-on-demand spine width formula. White paper: 0.002252" per page;
    cream: 0.0025" per page. The exact constants depend on the POD vendor
    (KDP, IngramSpark, etc.) — this is the most common set.
    """
    if paper == "cream":
        return page_count * 0.0025
    return page_count * 0.002252


def _wrap_canvas(
    trim_w_in: float,
    trim_h_in: float,
    spine_in: float,
    bleed_in: float,
    bg_color: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Create the wrap cover canvas (back + spine + front) with bleed.

    KDP wrap cover layout (left to right): bleed | back | spine | front | bleed
    Height (top to bottom): bleed | trim | bleed
    """
    total_w_in = bleed_in + trim_w_in + spine_in + trim_w_in + bleed_in
    total_h_in = bleed_in + trim_h_in + bleed_in
    total_w_px = _inches_to_pixels(total_w_in)
    total_h_px = _inches_to_pixels(total_h_in)
    return Image.new("RGB", (total_w_px, total_h_px), bg_color)


def _load_fonts() -> dict[str, ImageFont.FreeTypeFont]:
    """Load display fonts at TTF. Returns dict with 'title', 'body', 'author'.

    Baloo 2 (rounded, playful) for titles — the standard look of kids' book
    bestsellers. Quicksand (soft geometric) for body text. Falls back to
    Liberation Sans, then PIL default.
    """
    font_dir = Path(__file__).resolve().parents[3] / "assets" / "fonts"
    candidates = {
        "title": ["Baloo2-Bold.ttf", "LiberationSans-Bold.ttf"],
        "body": ["Quicksand-Regular.ttf", "LiberationSans-Regular.ttf"],
        "author": ["Baloo2-Bold.ttf", "LiberationSans-Bold.ttf"],
    }
    sizes = {"title": 96, "body": 28, "author": 32}
    fonts: dict[str, ImageFont.FreeTypeFont] = {}
    for key, names in candidates.items():
        loaded = None
        for name in names:
            try:
                loaded = ImageFont.truetype(str(font_dir / name), sizes[key])
                break
            except (OSError, FileNotFoundError):
                continue
        fonts[key] = loaded if loaded is not None else ImageFont.load_default()
    return fonts


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width_px: int,
    line_spacing: int = 6,
) -> list[tuple[str, int]]:
    """Word-wrap `text` to fit within `max_width_px`. Returns list of (line, width_px)."""
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    current = ""
    for w in words:
        candidate = (current + " " + w).strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width_px:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    # Measure widths
    measured = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        measured.append((line, bbox[2] - bbox[0]))
    return measured


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    cx: int,
    cy: int,
    fill: tuple[int, int, int] = (20, 20, 20),
    bg: tuple[int, int, int] | None = None,
    bg_padding: int = 20,
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Draw `text` centered at (cx, cy) with optional background box or stroke.

    A white stroke on dark text keeps the copy legible over any artwork
    without an opaque white box.
    """
    if not text:
        return
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if bg is not None:
        box = [
            cx - tw // 2 - bg_padding,
            cy - th // 2 - bg_padding,
            cx + tw // 2 + bg_padding,
            cy + th // 2 + bg_padding,
        ]
        draw.rectangle(box, fill=bg)
    draw.text(
        (cx - tw // 2, cy - th // 2),
        text,
        fill=fill,
        font=font,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )


def _paste_or_white(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    bg_image_path: Path | None,
    page_size_in: tuple[float, float],
    bg_color: tuple[int, int, int],
) -> None:
    """Paste a background image (or fill white) into a box of the canvas."""
    w_in, h_in = page_size_in
    w_px = _inches_to_pixels(w_in)
    h_px = _inches_to_pixels(h_in)
    if bg_image_path and bg_image_path.exists():
        with Image.open(bg_image_path) as im:
            cropped = ImageOps_fit(im, w_px, h_px)
        canvas.paste(cropped, box)
    else:
        draw = ImageDraw.Draw(canvas)
        draw.rectangle(box, fill=bg_color)


def _wrap_text_lines(
    text: str, font: ImageFont.ImageFont, max_width_px: int
) -> list[tuple[str, int]]:
    """Wrap text to fit `max_width_px`. Returns list of (line_text, line_width_px)."""
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        bbox = font.getbbox(candidate)
        cw = bbox[2] - bbox[0]
        if cw <= max_width_px:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    out = []
    for line in lines:
        bbox = font.getbbox(line)
        out.append((line, bbox[2] - bbox[0]))
    return out


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    max_w: int,
    start_size: int,
) -> ImageFont.FreeTypeFont:
    """Pick the largest size of `font_path` whose rendered width fits max_w."""
    size = start_size
    while size > 24:
        try:
            font = ImageFont.truetype(font_path, size)
        except OSError:
            break
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_w:
            return font
        size -= 8
    return ImageFont.load_default()


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    cx: int,
    cy: int,
    fill: tuple[int, int, int],
    shadow_offset: int = 6,
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Draw text centered at (cx, cy) with a soft drop shadow and optional
    white outline. No opaque box: the artwork stays visible."""
    if not text:
        return
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = cx - tw // 2, cy - th // 2
    # Drop shadow first (offset copy in a translucent dark tone)
    shadow = tuple(int(c * 0.45) for c in fill)
    draw.text(
        (x + shadow_offset, y + shadow_offset), text, fill=shadow,
        font=font, stroke_width=stroke_width, stroke_fill=shadow,
    )
    draw.text(
        (x, y), text, fill=fill, font=font,
        stroke_width=stroke_width, stroke_fill=stroke_fill,
    )


def _draw_rounded_panel(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int, int],
) -> None:
    """Draw a translucent rounded panel (soft sign/banner look) on `canvas`."""
    overlay = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle((0, 0, overlay.width - 1, overlay.height - 1), radius=radius, fill=fill)
    canvas.paste(overlay, (box[0], box[1]), overlay)


def build_wrap_cover(
    title: str,
    author: str,
    back_blurb: str,
    out_path: Path,
    page_count: int,
    page_size: str = "8.5x8.5",
    bleed_inches: float = 0.125,
    paper: str = "white",
    bg_color: tuple[int, int, int] = (255, 255, 255),
    text_color: tuple[int, int, int] = (30, 30, 50),
    bg_image_path: Path | None = None,
    hero_image_path: Path | None = None,
    back_image_path: Path | None = None,
    thumbnail_paths: list[Path] | None = None,
    age_range: str = "",
    language: str = "en",
    title_in_artwork: bool = True,
) -> Path:
    """Build a full KDP wrap cover (back + spine + front) with bleed.

    Layout (left to right): bleed | back | spine | front | bleed
    Height (top to bottom): bleed | trim | bleed

    Front: the localized hero artwork whose text zone already contains the
    exact title, age badge and author rendered by the image model. When the
    artwork does not carry the copy (title_in_artwork=False), the copy is
    composed deterministically with a white stroke — no opaque boxes.
    Back: one shared model-generated background (no character, no text) with
    SIX interior-page thumbnails (3 rows x 2 columns, large and centered)
    filling the upper area. A white lower-right reserve is kept clear for the
    ISBN barcode that KDP places automatically. No blurb text is drawn.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    trim_w_in, trim_h_in = map(float, page_size.lower().split("x"))
    spine_in = _spine_width_inches(page_count, paper=paper)

    canvas = _wrap_canvas(trim_w_in, trim_h_in, spine_in, bleed_inches, bg_color=bg_color)

    bleed_px = _inches_to_pixels(bleed_inches)
    trim_w_px = _inches_to_pixels(trim_w_in)
    trim_h_px = _inches_to_pixels(trim_h_in)
    spine_px = _inches_to_pixels(spine_in)

    # KDP layout: back | spine | front (left to right)
    back_box = (bleed_px, bleed_px, bleed_px + trim_w_px, bleed_px + trim_h_px)
    spine_x0 = bleed_px + trim_w_px
    spine_x1 = spine_x0 + spine_px
    front_x0 = spine_x1
    front_x1 = front_x0 + trim_w_px
    front_box = (front_x0, bleed_px, front_x1, bleed_px + trim_h_px)

    fonts = _load_fonts()
    title_font = fonts["title"]

    # Front: the localized hero artwork (title/copy embedded by the model).
    front_bg = hero_image_path or bg_image_path
    if front_bg and front_bg.exists():
        with Image.open(front_bg) as im:
            cropped = ImageOps_fit(im, trim_w_px, trim_h_px)
        canvas.paste(cropped, front_box)
    else:
        draw = ImageDraw.Draw(canvas)
        draw.rectangle(front_box, fill=bg_color)

    # Spine: plain white
    _paste_or_white(
        canvas,
        (spine_x0, bleed_px, spine_x1, bleed_px + trim_h_px),
        None,
        (spine_in, trim_h_in),
        bg_color,
    )

    # Back: shared model-generated background (no character, no text)
    if back_image_path and back_image_path.exists():
        with Image.open(back_image_path) as im:
            back_art = ImageOps_fit(im, trim_w_px, trim_h_px)
        canvas.paste(back_art, back_box)
    else:
        draw = ImageDraw.Draw(canvas)
        draw.rectangle(back_box, fill=bg_color)

    draw = ImageDraw.Draw(canvas)

    # ---- Front overlay: only when the artwork does NOT contain the copy.
    if not title_in_artwork:
        front_cx = front_x0 + trim_w_px // 2
        panel_w = int(trim_w_px * 0.86)
        panel_top = bleed_px + int(_inches_to_pixels(0.35))
        panel_bottom = bleed_px + int(_inches_to_pixels(1.35))
        # Fitted display font for the title
        font_dir = Path(__file__).resolve().parents[3] / "assets" / "fonts"
        title_font_path = font_dir / "Baloo2-Bold.ttf"
        try:
            title_font = _fit_font(draw, title, str(title_font_path), panel_w - 40, 150)
        except OSError:
            title_font = fonts["title"]
        # Layout inside the panel: title, author (age badge below)
        title_h = title_font.size
        author_h = fonts["author"].size
        badge_h = fonts["body"].size if age_range else 0
        total_h = title_h + author_h + badge_h + 3 * int(_inches_to_pixels(0.1))
        y = panel_top + (panel_bottom - panel_top - total_h) // 2
        # Translucent rounded panel (soft banner look, artwork stays visible)
        _draw_rounded_panel(
            canvas,
            (front_cx - panel_w // 2, panel_top, front_cx + panel_w // 2, panel_bottom),
            radius=48,
            fill=(255, 255, 255, 150),
        )
        y += title_h // 2
        _draw_text_with_shadow(
            draw, title, title_font, front_cx, y,
            fill=text_color, shadow_offset=5, stroke_width=4, stroke_fill=(255, 255, 255),
        )
        if author:
            y += title_h // 2 + author_h // 2 + int(_inches_to_pixels(0.1))
            _draw_text_with_shadow(
                draw, author, fonts["author"], front_cx, y,
                fill=text_color, shadow_offset=4, stroke_width=3, stroke_fill=(255, 255, 255),
            )
        if age_range:
            y += author_h // 2 + badge_h // 2 + int(_inches_to_pixels(0.08))
            _draw_text_with_shadow(
                draw, localized_age_label(language, age_range), fonts["body"], front_cx, y,
                fill=text_color, shadow_offset=3, stroke_width=2, stroke_fill=(255, 255, 255),
            )

    # ---- Back: 2 rows x 3 columns of large, centered thumbnails.
    # The grid fills the upper area and leaves the bottom ~1.5in clear for
    # the barcode reserve KDP needs.
    back_x0 = back_box[0]
    back_cx = back_x0 + trim_w_px // 2
    barcode_zone_in = 1.5
    grid_top_in = 0.4
    grid_bottom_in = trim_h_in - barcode_zone_in

    thumbs = [p for p in (thumbnail_paths or []) if p.exists()][:6]
    if thumbs:
        cols, rows = 3, 2
        gap_in = 0.18
        avail_w = trim_w_in - 2 * 0.5
        avail_h = grid_bottom_in - grid_top_in
        thumb_w_in = (avail_w - (cols - 1) * gap_in) / cols
        with Image.open(thumbs[0]) as first_thumb:
            source_ratio = first_thumb.width / first_thumb.height
        thumb_h_in = min(
            thumb_w_in / source_ratio,
            (avail_h - (rows - 1) * gap_in) / rows,
        )
        thumb_w = _inches_to_pixels(thumb_w_in)
        thumb_h = _inches_to_pixels(thumb_h_in)
        gap = _inches_to_pixels(gap_in)
        # Center the whole 3x2 grid vertically in the available area
        grid_h = rows * thumb_h + (rows - 1) * gap
        grid_y0 = bleed_px + int(_inches_to_pixels(grid_top_in)) + (int(_inches_to_pixels(avail_h)) - grid_h) // 2
        grid_w = cols * thumb_w + (cols - 1) * gap
        grid_x0 = back_cx - grid_w // 2
        for i, tp in enumerate(thumbs):
            col = i % cols
            row = i // cols
            x0 = grid_x0 + col * (thumb_w + gap)
            y0 = grid_y0 + row * (thumb_h + gap)
            with Image.open(tp) as im:
                t = ImageOps_contain(im, thumb_w, thumb_h, background=bg_color)
                # Trim the uniform white padding postprocess adds so the
                # thumbnail fills its cell with artwork instead of margins.
                ink = t.convert("L").point(lambda p: 255 if p < 128 else 0)
                bbox = ink.getbbox()
                if bbox:
                    pad = max(2, min(bbox[2] - bbox[0], bbox[3] - bbox[1]) // 30)
                    l = max(0, bbox[0] - pad)
                    top = max(0, bbox[1] - pad)
                    r = min(t.width, bbox[2] + pad)
                    bot = min(t.height, bbox[3] + pad)
                    t = t.crop((l, top, r, bot))
                t = ImageOps_contain(t, thumb_w, thumb_h, background=bg_color)
            cx = x0 + thumb_w // 2
            cy = y0 + thumb_h // 2
            canvas.paste(t, (cx - t.width // 2, cy - t.height // 2))
            draw.rectangle([x0, y0, x0 + thumb_w, y0 + thumb_h], outline=(200, 200, 200), width=2)

    # KDP adds its own 2 x 1.2in ISBN barcode at the lower right of the back
    # cover. Reserve a slightly larger white area so generated scenery cannot
    # be obscured or make the uploaded cover fail validation.
    barcode_reserve_w = _inches_to_pixels(2.25)
    barcode_reserve_h = _inches_to_pixels(1.45)
    draw.rectangle(
        [
            back_box[2] - barcode_reserve_w,
            back_box[3] - barcode_reserve_h,
            back_box[2],
            back_box[3],
        ],
        fill=(255, 255, 255),
    )

    canvas.save(out_path, "PNG", optimize=True)
    return out_path


def build_cover_pdf(wrap_png: Path, out_path: Path) -> Path:
    """Convert the wrap cover PNG into a single-page PDF (KDP cover format).

    KDP requires the cover as a single PDF containing back + spine + front.
    The PDF page size equals the wrap PNG dimensions at 300 DPI, so the
    geometry (bleed, spine, trim) is preserved exactly.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(wrap_png) as im:
        w_in = im.width / 300.0
        h_in = im.height / 300.0
    layout = img2pdf.get_layout_fun(pagesize=(w_in * 72, h_in * 72))
    out_path.write_bytes(img2pdf.convert([str(wrap_png)], layout_fun=layout))
    return out_path


def build_full_preview_pdf(cover_pdf: Path, interior_pdf: Path, out_path: Path) -> Path:
    """Combine the KDP wrap cover and interior into one review-only PDF."""
    from pypdf import PdfReader, PdfWriter

    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for source in (cover_pdf, interior_pdf):
        reader = PdfReader(str(source))
        for page in reader.pages:
            writer.add_page(page)
    with out_path.open("wb") as output:
        writer.write(output)
    return out_path


def embed_cover_title(image_path: Path, title: str) -> Path:
    """Embed exact localized cover copy into the hero artwork deterministically."""
    image_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as source:
        canvas = source.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    fonts = _load_fonts()
    cx = canvas.width // 2
    max_width = int(canvas.width * 0.84)
    title_font = fonts["title"]
    title_lines = _draw_wrapped_text(draw, title, title_font, max_width)
    line_height = title_font.size + 8
    title_height = line_height * len(title_lines)
    block_height = title_height + 44
    top = max(24, canvas.height - block_height - 48)
    draw.rounded_rectangle(
        [cx - max_width // 2 - 24, top - 20, cx + max_width // 2 + 24, top + block_height],
        radius=20,
        fill=(255, 255, 255),
    )
    for index, (line, width) in enumerate(title_lines):
        draw.text((cx - width // 2, top + index * line_height), line, fill=(30, 30, 50), font=title_font)
    canvas.save(image_path, "PNG", optimize=True)
    return image_path


def build_zip(image_paths: list[Path], out_path: Path) -> Path:
    """Build a ZIP of raw PNGs (for digital downloads)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in image_paths:
            zf.write(p, arcname=p.name)
    return out_path
