"""postprocess stage — deterministic image cleaning (B/W threshold, crop, etc.).

The processed PNG is the file that goes into the PDF, so it is the file that
gets validated. Reprocessing is content-addressed: if the source raw PNG
changes (new hash), the processed file is regenerated — a stale processed
file can never survive a source change.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import ClassVar

from PIL import Image, ImageOps

from productfoundry.domain.assets import AssetPlan
from productfoundry.engine.hashing import sha256_files
from productfoundry.engine.pipeline import Stage, StageContext

PROMPT_VERSION = "postprocess-v5"
INK_MARGIN_RATIO = 0.05


FRAME_EDGE_DARK_RATIO = 0.90
FRAME_ADJACENT_LIGHT_RATIO = 0.10
FRAME_MAX_STRIP_PX = 3


def _trim_edge_frame(bw: Image.Image) -> Image.Image:
    """Strip a thin full-edge frame the model sometimes draws around the image.

    Diffusion models occasionally wrap the artwork in a 1-3px border even when
    the prompt forbids it. Such a border makes the ink bbox cover the whole
    image, so the frame survives into the processed page as a visible rectangle
    at the safe-margin boundary. Detect it deterministically: an edge row/col
    that is overwhelmingly dark while its inward neighbor is mostly white is a
    frame line, not artwork — crop it before computing the ink bbox.
    """
    w, h = bw.size
    pixels = bw.load()

    def _line_is_frame(coord: int, horizontal: bool) -> bool:
        if horizontal:
            line = [pixels[c, coord] for c in range(w)]
            adj = coord + 1 if coord < h - 1 else coord - 1
            neighbor = [pixels[c, adj] for c in range(w)]
        else:
            line = [pixels[coord, r] for r in range(h)]
            adj = coord + 1 if coord < w - 1 else coord - 1
            neighbor = [pixels[adj, r] for r in range(h)]
        dark = sum(1 for p in line if p < 128) / len(line)
        light = sum(1 for p in neighbor if p > 127) / len(neighbor)
        return dark >= FRAME_EDGE_DARK_RATIO and light >= FRAME_ADJACENT_LIGHT_RATIO

    top = 0
    while top < h - 1 and top < FRAME_MAX_STRIP_PX and _line_is_frame(top, horizontal=True):
        top += 1
    bottom = h - 1
    while bottom > top and h - 1 - bottom < FRAME_MAX_STRIP_PX and _line_is_frame(bottom, horizontal=True):
        bottom -= 1
    left = 0
    while left < w - 1 and left < FRAME_MAX_STRIP_PX and _line_is_frame(left, horizontal=False):
        left += 1
    right = w - 1
    while right > left and w - 1 - right < FRAME_MAX_STRIP_PX and _line_is_frame(right, horizontal=False):
        right -= 1

    if (top, bottom, left, right) != (0, h - 1, 0, w - 1):
        bw = bw.crop((left, top, right + 1, bottom + 1))
    return bw


def to_grayscale_and_threshold(
    image_path: Path,
    out_path: Path,
    threshold: int = 128,
    target_dpi: int = 300,
    target_inches: float = 8.0,
    target_height_inches: float | None = None,
) -> Path:
    """Open image, convert to grayscale, normalize contrast, threshold to clean B/W,
    and upscale to print resolution while preserving aspect ratio. Ink is then
    fitted inside a uniform five-percent canvas margin.

    If target_height_inches is provided, the target dimensions are
    (target_inches x target_height_inches). Otherwise, the source's
    aspect ratio is preserved and the longest side is scaled to
    target_inches * target_dpi.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("L")
        im = ImageOps.autocontrast(im)
        bw = im.point(lambda p: 255 if p > threshold else 0)
        bw = bw.convert("L")
        bw = _trim_edge_frame(bw)
        if target_height_inches is not None:
            target_w = round(target_inches * target_dpi)
            target_h = round(target_height_inches * target_dpi)
        else:
            src_w, src_h = bw.size
            if src_w >= src_h:
                target_w = round(target_inches * target_dpi)
                target_h = round(target_w * src_h / src_w)
            else:
                target_h = round(target_inches * target_dpi)
                target_w = round(target_h * src_w / src_h)
        if bw.width < target_w or bw.height < target_h:
            bw = bw.resize((target_w, target_h), Image.LANCZOS)
            bw = bw.point(lambda p: 255 if p > threshold else 0)
        ink = bw.point(lambda p: 255 if p == 0 else 0)
        bbox = ink.getbbox()
        if bbox is not None:
            cropped = bw.crop(bbox)
            margin_x = math.ceil(target_w * INK_MARGIN_RATIO)
            margin_y = math.ceil(target_h * INK_MARGIN_RATIO)
            content_w = max(1, target_w - 2 * margin_x)
            content_h = max(1, target_h - 2 * margin_y)
            scale = min(content_w / cropped.width, content_h / cropped.height)
            fitted_size = (
                max(1, round(cropped.width * scale)),
                max(1, round(cropped.height * scale)),
            )
            fitted = cropped.resize(fitted_size, Image.LANCZOS)
            canvas = Image.new("L", (target_w, target_h), 255)
            canvas.paste(fitted, ((target_w - fitted.width) // 2, (target_h - fitted.height) // 2))
            # LANCZOS introduces antialiasing while fitting the ink; normalize
            # again so the line-art gate receives only black and white pixels.
            bw = canvas.point(lambda p: 255 if p > threshold else 0)
        bw.save(out_path, "PNG", optimize=True)
    return out_path


def process_assets(plan: AssetPlan, assets_dir: Path, processed_dir: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    processed_dir.mkdir(parents=True, exist_ok=True)
    for a in plan.assets:
        dst = processed_dir / f"{a.id}.png"
        if a.audit_status == "fail":
            # Skip assets that the image audit rejected — don't propagate to PDF.
            # Also remove any stale file from a previous run so it can't leak in.
            if dst.exists():
                dst.unlink()
            continue
        src = assets_dir / f"{a.id}.png"
        if not src.exists():
            continue
        # Content-addressed reprocessing: the processed file is only valid if
        # it was produced from the current source bytes.
        marker = processed_dir / f".{a.id}.src_hash"
        src_hash = sha256_files([src])
        marker_value = f"{PROMPT_VERSION}:{src_hash}"
        if not dst.exists() or not marker.exists() or marker.read_text().strip() != marker_value:
            to_grayscale_and_threshold(src, dst)
            marker.write_text(marker_value)
        out[a.id] = dst
    return out


class PostprocessStage(Stage):
    stage_name = "postprocess"
    inputs: ClassVar = ["assets"]
    outputs: ClassVar = ["processed"]
    input_models: ClassVar = {"assets": AssetPlan}
    prompt_version = PROMPT_VERSION

    def output_files(self, ctx: StageContext) -> list[Path]:
        return sorted(ctx.processed_dir.glob("page_*.png"))

    def expected_output_files(self, ctx: StageContext) -> list[Path] | None:
        # Declare expected processed PNGs from the assets artifact so the
        # executor can detect missing pages even before the stage runs.
        assets_env = ctx.get_artifact("assets")
        if assets_env is None:
            return None
        return [
            ctx.processed_dir / f"{a.get('id', f'page_{i+1:03d}')}.png"
            for i, a in enumerate(assets_env.artifact.get("assets", []))
            if a.get("audit_status") != "fail"
        ]

    def run(self, ctx: StageContext, assets: AssetPlan) -> AssetPlan:
        # Derive target dimensions from the pack's trim page_size
        try:
            trim_w_in, trim_h_in = map(float, ctx.pack.profile.page_size.lower().split("x"))
        except ValueError:
            trim_w_in, trim_h_in = 8.0, 8.0
        for a in assets.assets:
            if a.audit_status == "fail":
                dst = ctx.processed_dir / f"{a.id}.png"
                if dst.exists():
                    dst.unlink()
                continue
            src = ctx.assets_dir / f"{a.id}.png"
            if not src.exists():
                continue
            dst = ctx.processed_dir / f"{a.id}.png"
            marker = ctx.processed_dir / f".{a.id}.src_hash"
            src_hash = sha256_files([src])
            marker_value = f"{PROMPT_VERSION}:{src_hash}"
            if not dst.exists() or not marker.exists() or marker.read_text().strip() != marker_value:
                to_grayscale_and_threshold(
                    src, dst,
                    target_inches=trim_w_in,
                    target_height_inches=trim_h_in,
                )
                marker.write_text(marker_value)
        return assets
