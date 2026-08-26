"""lineart_check stage — deterministic validation of the final printable raster.

The processed PNG is what goes into the PDF, so this stage validates the
processed file itself (not the raw model output). All checks are pixel-level
and fail-closed: any doubt fails the pipeline.

Checks per page:
- file exists and is non-empty
- mode is L or 1 (no alpha, no color)
- only pixel values 0 and 255 (pure black/white, no gray)
- background is predominantly white (>= 90% of pixels)
- at least 0.5% black ink (a page with no lines is blank)
- ink stays at least 5% inside every canvas edge
- not fully blank
- dimensions match the expected print target (8.0in x 300 DPI = 2400px)
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from PIL import Image
from pydantic import BaseModel, Field

from productfoundry.domain.assets import AssetPlan
from productfoundry.engine.pipeline import Stage, StageContext

PROMPT_VERSION = "lineart-check-v3"

MIN_WHITE_RATIO = 0.90
MIN_BLACK_RATIO = 0.005
MIN_INK_MARGIN_RATIO = 0.05
FRAME_LINE_DARK_RATIO = 0.85
FRAME_MARGIN_RATIO = 0.07
TARGET_INCHES = 8.0
TARGET_DPI = 300


class LineArtCheckResult(BaseModel):
    asset_id: str
    status: str = "pass"  # pass | fail
    detail: str = ""


class LineArtCheckReport(BaseModel):
    verdict: str = "fail"  # pass | fail
    results: list[LineArtCheckResult] = Field(default_factory=list)


def _has_frame_line(gray: Image.Image, width: int, height: int) -> bool:
    """Detect a border frame: straight ink lines spanning ~90% of an edge band.

    A full-bleed frame the model drew around the artwork survives postprocess
    as a rectangle right at the safe-margin boundary. Detect a horizontal or
    vertical line that is overwhelmingly dark and lies within the outer
    `FRAME_MARGIN_RATIO` band of the canvas. Lines in the interior (e.g. a
    horizon) are legitimate art and do not count.
    """
    pixels = gray.load()
    band_x = max(1, round(width * FRAME_MARGIN_RATIO))
    band_y = max(1, round(height * FRAME_MARGIN_RATIO))
    dark_limit = round(FRAME_LINE_DARK_RATIO * width)
    for y in list(range(band_y)) + list(range(height - band_y, height)):
        if sum(1 for x in range(width) if pixels[x, y] < 128) >= dark_limit:
            return True
    dark_limit = round(FRAME_LINE_DARK_RATIO * height)
    for x in list(range(band_x)) + list(range(width - band_x, width)):
        if sum(1 for y in range(height) if pixels[x, y] < 128) >= dark_limit:
            return True
    return False


def _check_image(path: Path, target_w: int = 2400, target_h: int = 2400) -> LineArtCheckResult:
    if not path.exists() or path.stat().st_size == 0:
        return LineArtCheckResult(asset_id=path.stem, status="fail", detail="file missing or empty")
    try:
        with Image.open(path) as im:
            im.load()
            mode = im.mode
            if mode not in ("L", "1"):
                return LineArtCheckResult(
                    asset_id=path.stem, status="fail", detail=f"mode={mode} (expected L or 1)"
                )
            if im.width != target_w or im.height != target_h:
                return LineArtCheckResult(
                    asset_id=path.stem,
                    status="fail",
                    detail=f"size {im.width}x{im.height} (expected {target_w}x{target_h})",
                )
            gray = im.convert("L")
            hist = gray.histogram()
            total = sum(hist)
            if total == 0:
                return LineArtCheckResult(asset_id=path.stem, status="fail", detail="empty image")
            black = hist[0]
            white = hist[255]
            gray_pixels = total - black - white
            black_ratio = black / total
            white_ratio = white / total
            if gray_pixels > 0:
                return LineArtCheckResult(
                    asset_id=path.stem,
                    status="fail",
                    detail=f"{gray_pixels} gray pixels (only 0/255 allowed)",
                )
            if white_ratio < MIN_WHITE_RATIO:
                return LineArtCheckResult(
                    asset_id=path.stem,
                    status="fail",
                    detail=f"white ratio {white_ratio:.3f} < {MIN_WHITE_RATIO}",
                )
            if black_ratio < MIN_BLACK_RATIO:
                return LineArtCheckResult(
                    asset_id=path.stem,
                    status="fail",
                    detail=f"black ratio {black_ratio:.4f} < {MIN_BLACK_RATIO} (page looks blank)",
                )
            ink_bbox = gray.point(lambda p: 255 if p == 0 else 0).getbbox()
            if ink_bbox is None:
                return LineArtCheckResult(asset_id=path.stem, status="fail", detail="page has no ink")
            min_x = round(im.width * MIN_INK_MARGIN_RATIO)
            min_y = round(im.height * MIN_INK_MARGIN_RATIO)
            max_x = im.width - min_x
            max_y = im.height - min_y
            if ink_bbox[0] < min_x or ink_bbox[1] < min_y or ink_bbox[2] > max_x or ink_bbox[3] > max_y:
                return LineArtCheckResult(
                    asset_id=path.stem,
                    status="fail",
                    detail="ink reaches the trim margin",
                )
            if _has_frame_line(gray, im.width, im.height):
                return LineArtCheckResult(
                    asset_id=path.stem,
                    status="fail",
                    detail="frame/border detected around the artwork",
                )
            return LineArtCheckResult(
                asset_id=path.stem,
                status="pass",
                detail=f"mode={mode} {im.width}x{im.height} black={black_ratio:.3f} white={white_ratio:.3f}",
            )
    except OSError as e:
        return LineArtCheckResult(asset_id=path.stem, status="fail", detail=f"decode error: {e}")


class LineArtCheckStage(Stage):
    stage_name = "lineart_check"
    inputs: ClassVar = ["assets"]
    outputs: ClassVar = ["lineart_check"]
    input_models: ClassVar = {"assets": AssetPlan}
    prompt_version = PROMPT_VERSION
    gate_verdict = "pass"

    def output_files(self, ctx: StageContext) -> list[Path]:
        return []

    def run(self, ctx: StageContext, assets: AssetPlan) -> LineArtCheckReport:
        results: list[LineArtCheckResult] = []
        # Derive expected target from the pack's page_size (trim in inches)
        try:
            trim_w_in, trim_h_in = map(float, ctx.pack.profile.page_size.lower().split("x"))
        except ValueError:
            trim_w_in, trim_h_in = 8.0, 8.0
        target_w = round(trim_w_in * TARGET_DPI)
        target_h = round(trim_h_in * TARGET_DPI)
        for a in assets.assets:
            if a.audit_status == "fail":
                results.append(
                    LineArtCheckResult(asset_id=a.id, status="fail", detail="asset rejected by judge")
                )
                continue
            results.append(_check_image(ctx.processed_dir / f"{a.id}.png", target_w, target_h))
        verdict = "pass" if results and all(r.status == "pass" for r in results) else "fail"
        return LineArtCheckReport(verdict=verdict, results=results)
