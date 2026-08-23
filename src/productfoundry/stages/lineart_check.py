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
- not fully blank
- dimensions match the expected print target (8.0in x 300 DPI = 2400px)
"""
from __future__ import annotations
from pathlib import Path

from pydantic import BaseModel, Field
from PIL import Image

from productfoundry.domain.assets import AssetPlan
from productfoundry.engine.pipeline import Stage, StageContext


PROMPT_VERSION = "lineart-check-v1"

MIN_WHITE_RATIO = 0.90
MIN_BLACK_RATIO = 0.005
TARGET_INCHES = 8.0
TARGET_DPI = 300


class LineArtCheckResult(BaseModel):
    asset_id: str
    status: str = "pass"  # pass | fail
    detail: str = ""


class LineArtCheckReport(BaseModel):
    verdict: str = "fail"  # pass | fail
    results: list[LineArtCheckResult] = Field(default_factory=list)


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
            return LineArtCheckResult(
                asset_id=path.stem,
                status="pass",
                detail=f"mode={mode} {im.width}x{im.height} black={black_ratio:.3f} white={white_ratio:.3f}",
            )
    except Exception as e:
        return LineArtCheckResult(asset_id=path.stem, status="fail", detail=f"decode error: {e}")


class LineArtCheckStage(Stage):
    stage_name = "lineart_check"
    inputs = ["assets"]
    outputs = ["lineart_check"]
    input_models = {"assets": AssetPlan}
    prompt_version = PROMPT_VERSION
    gate_verdict = "pass"

    def output_files(self, ctx: StageContext) -> list[Path]:
        return []

    def run(self, ctx: StageContext, assets: AssetPlan) -> LineArtCheckReport:
        results: list[LineArtCheckResult] = []
        # Derive expected target from the pack's page_size (trim in inches)
        try:
            trim_w_in, trim_h_in = map(float, ctx.pack.profile.page_size.lower().split("x"))
        except Exception:
            trim_w_in, trim_h_in = 8.0, 8.0
        target_w = int(round(trim_w_in * TARGET_DPI))
        target_h = int(round(trim_h_in * TARGET_DPI))
        for a in assets.assets:
            if a.audit_status == "fail":
                results.append(
                    LineArtCheckResult(asset_id=a.id, status="fail", detail="asset rejected by judge")
                )
                continue
            results.append(_check_image(ctx.processed_dir / f"{a.id}.png", target_w, target_h))
        verdict = "pass" if results and all(r.status == "pass" for r in results) else "fail"
        return LineArtCheckReport(verdict=verdict, results=results)
