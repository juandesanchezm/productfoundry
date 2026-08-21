"""Deterministic packaging — build digital PDFs, print-ready PDFs (with bleed), and ZIPs."""
from __future__ import annotations
import io
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import img2pdf


def _inches_to_pixels(inches: float, dpi: int = 300) -> int:
    return int(round(inches * dpi))


def build_pdf(image_paths: list[Path], out_path: Path, page_size: str = "8.5x11", bleed: float = 0.0) -> Path:
    """Build a PDF from a list of image paths. Pure deterministic, no LLM."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # img2pdf wants exact pixel sizes from the images themselves.
    # To control page size, we resize source images to target page size in pixels.
    w_in, h_in = map(float, page_size.split("x"))
    target_w = _inches_to_pixels(w_in + 2 * bleed)
    target_h = _inches_to_pixels(h_in + 2 * bleed)

    resized: list[bytes] = []
    for src in image_paths:
        with Image.open(src) as im:
            im = ImageOps_fit(im, target_w, target_h)
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
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
        new_w = int(round(im.height * target_ratio))
        left = (im.width - new_w) // 2
        im = im.crop((left, 0, left + new_w, im.height))
    else:
        # source is taller — crop top/bottom
        new_h = int(round(im.width / target_ratio))
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
    """Build a simple cover image. Deterministic, no LLM."""
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
    # Use default font (PIL only ships default PIL Font, no TTF needed for MVP)
    title_font = ImageFont.load_default(80)
    sub_font = ImageFont.load_default(40)

    # Center title with a simple white box background for readability
    if title:
        bbox = draw.textbbox((0, 0), title, font=title_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        margin = 40
        pad = 20
        box = [w // 2 - tw // 2 - pad, h // 2 - th // 2 - pad, w // 2 + tw // 2 + pad, h // 2 + th // 2 + pad]
        # Soft white overlay
        draw.rectangle(box, fill=(255, 255, 255, 230))
        draw.text((w // 2 - tw // 2, h // 2 - th // 2), title, fill=text_color, font=title_font)

    if subtitle:
        bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((w // 2 - tw // 2, h // 2 + 80), subtitle, fill=text_color, font=sub_font)

    canvas.save(out_path, "PNG", optimize=True)
    return out_path


def build_zip(image_paths: list[Path], out_path: Path) -> Path:
    """Build a ZIP of raw PNGs (for digital downloads)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in image_paths:
            zf.write(p, arcname=p.name)
    return out_path
