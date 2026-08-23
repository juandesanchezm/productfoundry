"""postprocess stage — deterministic image cleaning (B/W threshold, crop, etc.).

The processed PNG is the file that goes into the PDF, so it is the file that
gets validated. Reprocessing is content-addressed: if the source raw PNG
changes (new hash), the processed file is regenerated — a stale processed
file can never survive a source change.
"""
from __future__ import annotations
from pathlib import Path

from PIL import Image, ImageOps

from productfoundry.domain.assets import AssetPlan
from productfoundry.engine.hashing import sha256_files
from productfoundry.engine.pipeline import Stage, StageContext


PROMPT_VERSION = "postprocess-v2"


def to_grayscale_and_threshold(
    image_path: Path,
    out_path: Path,
    threshold: int = 128,
    target_dpi: int = 300,
    target_inches: float = 8.0,
    target_height_inches: float | None = None,
) -> Path:
    """Open image, convert to grayscale, normalize contrast, threshold to clean B/W,
    and upscale to print resolution while preserving aspect ratio.

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
        if target_height_inches is not None:
            target_w = int(round(target_inches * target_dpi))
            target_h = int(round(target_height_inches * target_dpi))
        else:
            src_w, src_h = bw.size
            if src_w >= src_h:
                target_w = int(round(target_inches * target_dpi))
                target_h = int(round(target_w * src_h / src_w))
            else:
                target_h = int(round(target_inches * target_dpi))
                target_w = int(round(target_h * src_w / src_h))
        if bw.width < target_w or bw.height < target_h:
            bw = bw.resize((target_w, target_h), Image.LANCZOS)
            bw = bw.point(lambda p: 255 if p > threshold else 0)
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
        if not dst.exists() or not marker.exists() or marker.read_text().strip() != src_hash:
            to_grayscale_and_threshold(src, dst)
            marker.write_text(src_hash)
        out[a.id] = dst
    return out


class PostprocessStage(Stage):
    stage_name = "postprocess"
    inputs = ["assets"]
    outputs = ["processed"]
    input_models = {"assets": AssetPlan}
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
        except Exception:
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
            if not dst.exists() or not marker.exists() or marker.read_text().strip() != src_hash:
                to_grayscale_and_threshold(
                    src, dst,
                    target_inches=trim_w_in,
                    target_height_inches=trim_h_in,
                )
                marker.write_text(src_hash)
        return assets
