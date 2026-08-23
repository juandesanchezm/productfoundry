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


def build_cover(
    title: str,
    subtitle: str,
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
    sub_font = ImageFont.load_default(40)

    if title:
        bbox = draw.textbbox((0, 0), title, font=title_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = 20
        box = [w // 2 - tw // 2 - pad, h // 2 - th // 2 - pad, w // 2 + tw // 2 + pad, h // 2 + th // 2 + pad]
        draw.rectangle(box, fill=(255, 255, 255, 230))
        draw.text((w // 2 - tw // 2, h // 2 - th // 2), title, fill=text_color, font=title_font)

    if subtitle:
        bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((w // 2 - tw // 2, h // 2 + 80), subtitle, fill=text_color, font=sub_font)

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
    """Load display fonts at TTF. Returns dict with 'title', 'subtitle', 'body', 'author'.

    Baloo 2 (rounded, playful) for titles — the standard look of kids' book
    bestsellers. Quicksand (soft geometric) for body text. Falls back to
    Liberation Sans, then PIL default.
    """
    font_dir = Path(__file__).resolve().parents[3] / "assets" / "fonts"
    candidates = {
        "title": ["Baloo2-Bold.ttf", "LiberationSans-Bold.ttf"],
        "subtitle": ["Quicksand-Bold.ttf", "LiberationSans-Bold.ttf"],
        "body": ["Quicksand-Regular.ttf", "LiberationSans-Regular.ttf"],
        "author": ["Baloo2-Bold.ttf", "LiberationSans-Bold.ttf"],
    }
    sizes = {"title": 96, "subtitle": 36, "body": 28, "author": 32}
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
) -> None:
    """Draw `text` centered at (cx, cy) with optional background box."""
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
    draw.text((cx - tw // 2, cy - th // 2), text, fill=fill, font=font)


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


def build_wrap_cover(
    title: str,
    subtitle: str,
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
    thumbnail_paths: list[Path] | None = None,
    age_range: str = "",
    title_in_artwork: bool = False,
) -> Path:
    """Build a full KDP wrap cover (back + spine + front) with bleed.

    Layout (left to right): bleed | back | spine | front | bleed
    Height (top to bottom): bleed | trim | bleed

    Back: up to 4 interior-page thumbnails in a 2x2 grid at the top, the blurb
    in the middle, and a barcode placeholder at the bottom.
    Front: hero artwork (or bg image) with the title in a soft white pill
    (Baloo 2 rounded display font) and an optional age badge ("Ages 3-8").
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
    subtitle_font = fonts["subtitle"]
    body_font = fonts["body"]
    author_font = fonts["author"]

    # Front: hero artwork (or bg image) or plain white
    front_bg = hero_image_path or bg_image_path
    if front_bg and front_bg.exists():
        with Image.open(front_bg) as im:
            cropped = ImageOps_fit(im, trim_w_px, trim_h_px)
        canvas.paste(cropped, front_box)
    else:
        draw = ImageDraw.Draw(canvas)
        draw.rectangle(front_box, fill=bg_color)

    # Spine and back: plain white
    _paste_or_white(
        canvas,
        (spine_x0, bleed_px, spine_x1, bleed_px + trim_h_px),
        None,
        (spine_in, trim_h_in),
        bg_color,
    )
    _paste_or_white(
        canvas,
        back_box,
        None,
        (trim_w_in, trim_h_in),
        bg_color,
    )

    draw = ImageDraw.Draw(canvas)

    # ---- Front: title + subtitle stacked towards the bottom (so the artwork is the hero)
    front_cx = front_x0 + trim_w_px // 2
    front_bottom = bleed_px + trim_h_px - int(_inches_to_pixels(0.6))
    if not title_in_artwork:
        # Title in a soft white pill. When the hero already contains the exact
        # localized title, the wrapper must not duplicate it.
        title_bg_pad_x = 28
        title_bg_pad_y = 14
        bbox = draw.textbbox((0, 0), title, font=title_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        title_box = [
            front_cx - tw // 2 - title_bg_pad_x,
            front_bottom - th - 2 * title_bg_pad_y,
            front_cx + tw // 2 + title_bg_pad_x,
            front_bottom,
        ]
        draw.rectangle(title_box, fill=(255, 255, 255, 230))
        draw.text(
            (front_cx - tw // 2, front_bottom - th - title_bg_pad_y),
            title,
            fill=text_color,
            font=title_font,
        )

        # Subtitle below the title (no background)
        if subtitle:
            bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
            sw = bbox[2] - bbox[0]
            sub_y = front_bottom + 20
            draw.text((front_cx - sw // 2, sub_y), subtitle, fill=text_color, font=subtitle_font)

    # Age badge in the top-right corner of the front (bestseller convention)
    if age_range:
        badge_text = f"Ages {age_range}"
        badge_font = fonts["subtitle"]
        bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = 12
        badge_x1 = front_box[2] - int(_inches_to_pixels(0.25))
        badge_y0 = front_box[1] + int(_inches_to_pixels(0.25))
        draw.rounded_rectangle(
            [badge_x1 - bw - 2 * pad, badge_y0, badge_x1, badge_y0 + bh + 2 * pad],
            radius=pad,
            fill=(255, 255, 255),
            outline=text_color,
            width=2,
        )
        draw.text((badge_x1 - bw - pad, badge_y0 + pad), badge_text, fill=text_color, font=badge_font)

    # ---- Spine: title + author rotated (text reads top-to-bottom)
    if spine_px > 0:
        # Create a tall, narrow label that will be rotated 90°. After rotation,
        # the label's height becomes its width (which must fit within spine_px)
        # and its width becomes its height (which must fit within trim_h_px).
        label_h_px = max(8, spine_px)  # becomes width after rotation
        label_w_px = max(80, _inches_to_pixels(trim_h_in * 0.7))  # becomes height after rotation
        label = Image.new("RGB", (label_w_px, label_h_px), bg_color)
        label_draw = ImageDraw.Draw(label)
        spine_text = f"{title} • {author}"
        bbox = label_draw.textbbox((0, 0), spine_text, font=body_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        # Center text in the label (before rotation)
        label_draw.text(
            ((label_w_px - tw) // 2, (label_h_px - th) // 2),
            spine_text,
            fill=text_color,
            font=body_font,
        )
        rotated = label.rotate(90, expand=True)
        rot_w, rot_h = rotated.size
        spine_cy = bleed_px + trim_h_px // 2
        # Clamp the rotated label to the spine area to prevent overflow
        paste_x = spine_x0 + max(0, (spine_px - rot_w) // 2)
        paste_y = max(bleed_px, spine_cy - rot_h // 2)
        canvas.paste(rotated, (paste_x, paste_y))

    # ---- Back: thumbnails at top, author, blurb in the middle, barcode at bottom
    back_x0 = back_box[0]
    back_cx = back_x0 + trim_w_px // 2
    back_pad = int(_inches_to_pixels(0.4))

    # Thumbnail grid 2x2 (up to 4 interior pages) at the top of the back cover
    thumb_y = bleed_px + int(_inches_to_pixels(0.5))
    if thumbnail_paths:
        thumbs = [p for p in thumbnail_paths if p.exists()][:4]
        if thumbs:
            gap = int(_inches_to_pixels(0.15))
            thumb_w = (trim_w_px - 2 * back_pad - gap) // 2
            thumb_h = int(thumb_w * 0.9)  # slightly shorter than wide
            for i, tp in enumerate(thumbs):
                col = i % 2
                row = i // 2
                x0 = back_x0 + back_pad + col * (thumb_w + gap)
                y0 = thumb_y + row * (thumb_h + gap)
                with Image.open(tp) as im:
                    t = ImageOps_fit(im, thumb_w, thumb_h)
                canvas.paste(t, (x0, y0))
                draw.rectangle([x0, y0, x0 + thumb_w, y0 + thumb_h], outline=(200, 200, 200), width=2)
            thumb_bottom = thumb_y + 2 * (thumb_h + gap) - gap
        else:
            thumb_bottom = thumb_y
    else:
        thumb_bottom = thumb_y

    # Author below the thumbnails, bold
    author_y = thumb_bottom + int(_inches_to_pixels(0.25))
    if author:
        bbox = draw.textbbox((0, 0), author, font=author_font)
        aw, ah = bbox[2] - bbox[0], bbox[3] - bbox[1]
        ax_pad = 16
        draw.rectangle(
            [
                back_cx - aw // 2 - ax_pad,
                author_y - ax_pad,
                back_cx + aw // 2 + ax_pad,
                author_y + ah + ax_pad,
            ],
            fill=(255, 255, 255),
        )
        draw.text((back_cx - aw // 2, author_y), author, fill=text_color, font=author_font)

    # Blurb in the middle (between author and barcode)
    blurb_max_w_px = trim_w_px - 2 * back_pad
    blurb_lines = _wrap_text_lines(back_blurb, body_font, blurb_max_w_px)
    line_height = body_font.size + 8
    blurb_total_h = line_height * len(blurb_lines)
    bc_h_px = _inches_to_pixels(0.7)
    bc_y = bleed_px + trim_h_px - bc_h_px - int(_inches_to_pixels(0.3))
    blurb_zone_top = author_y + int(_inches_to_pixels(0.4))
    blurb_zone_bottom = bc_y - int(_inches_to_pixels(0.3))
    blurb_y_start = blurb_zone_top + (blurb_zone_bottom - blurb_zone_top - blurb_total_h) // 2
    # Truncate to fit the zone
    max_lines = max(4, (blurb_zone_bottom - blurb_zone_top) // line_height)
    if len(blurb_lines) > max_lines:
        blurb_lines = blurb_lines[:max_lines]
        blurb_lines[-1] = (blurb_lines[-1][0] + "...", blurb_lines[-1][1])
    for i, (line, _) in enumerate(blurb_lines):
        bbox = draw.textbbox((0, 0), line, font=body_font)
        lw = bbox[2] - bbox[0]
        y = blurb_y_start + i * line_height
        bx_pad = 8
        draw.rectangle(
            [
                back_cx - lw // 2 - bx_pad,
                y - bx_pad,
                back_cx + lw // 2 + bx_pad,
                y + line_height - bx_pad,
            ],
            fill=(255, 255, 255),
        )
        draw.text((back_cx - lw // 2, y), line, fill=text_color, font=body_font)

    # Reserve the KDP barcode area by leaving it blank. KDP inserts a real
    # barcode from the ISBN metadata; drawing synthetic bars is misleading.
    bc_w_px = _inches_to_pixels(1.5)
    bc_x = back_cx - bc_w_px // 2
    draw.rectangle([bc_x, bc_y, bc_x + bc_w_px, bc_y + bc_h_px], fill=(255, 255, 255))

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


def embed_cover_title(image_path: Path, title: str, subtitle: str = "") -> Path:
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
    subtitle_font = fonts["subtitle"]
    subtitle_height = subtitle_font.size + 12 if subtitle else 0
    block_height = title_height + subtitle_height + 44
    top = max(24, canvas.height - block_height - 48)
    draw.rounded_rectangle(
        [cx - max_width // 2 - 24, top - 20, cx + max_width // 2 + 24, top + block_height],
        radius=20,
        fill=(255, 255, 255),
    )
    for index, (line, width) in enumerate(title_lines):
        draw.text((cx - width // 2, top + index * line_height), line, fill=(30, 30, 50), font=title_font)
    if subtitle:
        bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
        draw.text(
            (cx - (bbox[2] - bbox[0]) // 2, top + title_height + 10),
            subtitle,
            fill=(30, 30, 50),
            font=subtitle_font,
        )
    canvas.save(image_path, "PNG", optimize=True)
    return image_path


def build_zip(image_paths: list[Path], out_path: Path) -> Path:
    """Build a ZIP of raw PNGs (for digital downloads)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in image_paths:
            zf.write(p, arcname=p.name)
    return out_path
