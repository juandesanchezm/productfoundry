"""postprocess stage — deterministic image cleaning (B/W threshold, crop, etc.)."""
from __future__ import annotations
from pathlib import Path

from pydantic import BaseModel
from PIL import Image, ImageOps

from productfoundry.domain.assets import AssetPlan
from productfoundry.engine.pipeline import Stage, StageContext


PROMPT_VERSION = "postprocess-v1"


def to_grayscale_and_threshold(image_path: Path, out_path: Path, threshold: int = 200) -> Path:
    """Open image, convert to grayscale, threshold to clean B/W. Suitable for line-art output."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("L")  # grayscale
        # Apply threshold: pixels above threshold become white, below become black
        bw = im.point(lambda p: 255 if p > threshold else 0)
        bw = bw.convert("1")  # bilevel
        bw.save(out_path, "PNG", optimize=True)
    return out_path


def process_assets(plan: AssetPlan, assets_dir: Path, processed_dir: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    processed_dir.mkdir(parents=True, exist_ok=True)
    for a in plan.assets:
        src = assets_dir / f"{a.id}.png"
        dst = processed_dir / f"{a.id}.png"
        if not dst.exists():
            to_grayscale_and_threshold(src, dst)
        out[a.id] = dst
    return out


class PostprocessStage(Stage):
    stage_name = "postprocess"
    inputs = ["assets"]
    outputs = ["processed"]
    input_models = {"assets": AssetPlan}
    prompt_version = PROMPT_VERSION

    def run(self, ctx: StageContext, assets: AssetPlan) -> AssetPlan:
        # Returns the same AssetPlan; the side-effect is the processed files on disk.
        process_assets(assets, ctx.assets_dir, ctx.processed_dir)
        return assets
