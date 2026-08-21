"""package stage — deterministic build of digital + print deliverables across languages."""
from __future__ import annotations
from pathlib import Path

from pydantic import BaseModel

from productfoundry.domain.assets import AssetPlan
from productfoundry.domain.pack import FormatSpec
from productfoundry.domain.packaging import PackageOutput, PackagePlan
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.packaging import build_cover, build_pdf, build_zip


PROMPT_VERSION = "package-v1"


def _packaging_spec(pack, format_kind: str) -> dict:
    if not pack.packaging:
        return {}
    return pack.packaging.get(format_kind, {}) or {}


def build_packages(
    assets: AssetPlan,
    plan: ProductPlan,
    pack,
    processed_dir: Path,
    packages_dir: Path,
    languages: list[str],
    formats: list[str],
) -> PackagePlan:
    image_paths = [processed_dir / f"{a.id}.png" for a in assets.assets]
    out: list[PackageOutput] = []

    spec = _packaging_spec(pack, "digital")
    digital_size = spec.get("page_size", pack.profile.image_size)
    if "digital" in formats:
        fs = pack.profile.formats.digital
        for marketplace in fs.marketplaces:
            for lang in languages:
                # Digital PDF
                pdf_path = packages_dir / "digital" / lang / f"{plan.pack_id}-{plan.theme}-{marketplace}.pdf"
                build_pdf(image_paths, pdf_path, page_size=digital_size, bleed=0.0)
                out.append(
                    PackageOutput(
                        format="digital",
                        language=lang,
                        marketplace=marketplace,
                        path=str(pdf_path),
                        file_size=pdf_path.stat().st_size,
                    )
                )
                # ZIP of PNGs (for digital downloads)
                zip_path = packages_dir / "digital" / lang / f"{plan.pack_id}-{plan.theme}-{marketplace}.zip"
                build_zip(image_paths, zip_path)
                out.append(
                    PackageOutput(
                        format="digital",
                        language=lang,
                        marketplace=marketplace,
                        path=str(zip_path),
                        file_size=zip_path.stat().st_size,
                    )
                )

    if "print" in formats:
        ps = pack.profile.formats.print
        spec = _packaging_spec(pack, "print")
        print_size = spec.get("page_size", "8.5x11")
        bleed = float(spec.get("bleed_inches", 0.125))
        for marketplace in ps.marketplaces:
            for lang in languages:
                # Interior PDF (with bleed for print marketplace)
                interior_path = (
                    packages_dir
                    / "print"
                    / lang
                    / f"{plan.pack_id}-{plan.theme}-{marketplace}-interior.pdf"
                )
                build_pdf(image_paths, interior_path, page_size=print_size, bleed=bleed)
                out.append(
                    PackageOutput(
                        format="print",
                        language=lang,
                        marketplace=marketplace,
                        path=str(interior_path),
                        file_size=interior_path.stat().st_size,
                    )
                )
                # Cover (one per language, since title differs)
                cover_path = (
                    packages_dir
                    / "print"
                    / lang
                    / f"{plan.pack_id}-{plan.theme}-{marketplace}-cover.png"
                )
                title = plan.titles.get(lang, plan.titles.get("en", plan.theme))
                build_cover(
                    title=title,
                    subtitle=plan.subtitle,
                    out_path=cover_path,
                    page_size=print_size,
                )
                out.append(
                    PackageOutput(
                        format="print",
                        language=lang,
                        marketplace=marketplace,
                        path=str(cover_path),
                        file_size=cover_path.stat().st_size,
                    )
                )

    return PackagePlan(packages=out)


class PackageStage(Stage):
    stage_name = "package"
    inputs = ["concept", "assets"]
    outputs = ["packages"]
    input_models = {"concept": ProductPlan, "assets": AssetPlan}
    prompt_version = PROMPT_VERSION

    def run(self, ctx: StageContext, concept: ProductPlan, assets: AssetPlan) -> PackagePlan:
        return build_packages(
            assets=assets,
            plan=concept,
            pack=ctx.pack,
            processed_dir=ctx.processed_dir,
            packages_dir=ctx.packages_dir,
            languages=ctx.request.languages,
            formats=ctx.request.formats,
        )
