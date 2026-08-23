"""package stage — deterministic build of digital + print deliverables across languages."""
from __future__ import annotations

from pathlib import Path

from productfoundry.domain.assets import AssetPlan
from productfoundry.domain.packaging import PackageOutput, PackagePlan
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.packaging import (
    build_cover_pdf,
    build_full_preview_pdf,
    build_pdf,
    build_wrap_cover,
    build_zip,
)

PROMPT_VERSION = "package-v3"


def _packaging_spec(pack, format_kind: str) -> dict:
    if not pack.packaging:
        return {}
    return pack.packaging.get(format_kind, {}) or {}


def _lookup_story(pack, story_id: str) -> dict | None:
    if not story_id:
        return None
    stories = (getattr(pack, "stories", None) or {})
    if not isinstance(stories, dict):
        return None
    for s in stories.get("stories", []) or []:
        if isinstance(s, dict) and s.get("id") == story_id:
            return s
    return None


def _get_description_blurb(pack, plan: ProductPlan, lang: str, story: dict | None) -> str:
    if story:
        if lang == "es":
            return story.get("description_blurb_es", story.get("description_blurb_en", ""))
        return story.get("description_blurb_en", "")
    return plan.description_hint or ""


def _get_author(pack) -> str:
    profile = getattr(pack, "profile", None)
    if profile is None:
        return ""
    return getattr(profile, "author", "") or ""


def build_packages(
    assets: AssetPlan,
    plan: ProductPlan,
    pack,
    request_theme: str,
    request_story_id: str,
    request_page_count: int,
    processed_dir: Path,
    packages_dir: Path,
    assets_dir: Path,
    languages: list[str],
    formats: list[str],
) -> PackagePlan:
    if len(assets.assets) != request_page_count:
        raise RuntimeError(
            f"asset/page count mismatch: {len(assets.assets)} assets for {request_page_count} requested pages"
        )
    missing: list[str] = []
    image_paths: list[Path] = []
    for asset in assets.assets:
        path = processed_dir / f"{asset.id}.png"
        if asset.audit_status == "fail":
            missing.append(f"{asset.id}: failed audit")
        elif not path.exists() or path.stat().st_size == 0:
            missing.append(f"{asset.id}: processed image missing")
        else:
            image_paths.append(path)
    if missing:
        raise RuntimeError("cannot package incomplete page set: " + "; ".join(missing))
    out: list[PackageOutput] = []

    spec = _packaging_spec(pack, "digital")
    digital_size = spec.get("page_size", pack.profile.image_size)
    if "digital" in formats:
        fs = pack.profile.formats.digital
        for marketplace in fs.marketplaces:
            for lang in languages:
                pdf_path = (
                    packages_dir / "digital" / lang
                    / f"{plan.pack_id}-{request_theme}-{marketplace}.pdf"
                )
                build_pdf(image_paths, pdf_path, page_size=digital_size, bleed=0.0)
                out.append(
                    PackageOutput(format="digital", language=lang, marketplace=marketplace,
                                 path=str(pdf_path), file_size=pdf_path.stat().st_size)
                )
                zip_path = (
                    packages_dir / "digital" / lang
                    / f"{plan.pack_id}-{request_theme}-{marketplace}.zip"
                )
                build_zip(image_paths, zip_path)
                out.append(
                    PackageOutput(format="digital", language=lang, marketplace=marketplace,
                                 path=str(zip_path), file_size=zip_path.stat().st_size)
                )

    if "print" in formats:
        ps = pack.profile.formats.print
        spec = _packaging_spec(pack, "print")
        print_size = spec.get("page_size", "8.5x8.5")
        bleed = float(spec.get("bleed_inches", 0.125))
        paper = spec.get("paper", "white")
        author = _get_author(pack)
        story = _lookup_story(pack, request_story_id)

        for marketplace in ps.marketplaces:
            for lang in languages:
                # Interior PDF (with bleed)
                interior_path = (
                    packages_dir / "print" / lang
                    / f"{plan.pack_id}-{request_theme}-{marketplace}-interior.pdf"
                )
                build_pdf(image_paths, interior_path, page_size=print_size, bleed=bleed)
                out.append(
                    PackageOutput(format="print", language=lang, marketplace=marketplace,
                                 path=str(interior_path), file_size=interior_path.stat().st_size)
                )

                # Wrap cover (front + spine + back) with KDP-compliant dimensions
                wrap_path = (
                    packages_dir / "print" / lang
                    / f"{plan.pack_id}-{request_theme}-{marketplace}-cover.png"
                )
                title = plan.titles.get(lang, plan.titles.get("en", request_theme))
                subtitle = plan.subtitle
                blurb = _get_description_blurb(pack, plan, lang, story)
                # The hero artwork is generated by the hero stage and lives at
                # assets/cover_hero.png. It is the full-color dedicated cover
                # image; falls back to the first page if not yet generated.
                hero_img = assets_dir / f"cover_hero_{lang}.png"
                if not hero_img.exists():
                    fallback = assets_dir / "cover_hero.png"
                    hero_img = fallback if fallback.exists() else (image_paths[0] if image_paths else None)
                # Interior-page thumbnails for the back cover (bestseller convention)
                thumbnails = [image_paths[i] for i in (0, 2, 4, 6) if i < len(image_paths)]
                age_range = getattr(pack.profile, "age_range", "") or ""
                build_wrap_cover(
                    title=title,
                    subtitle=subtitle,
                    author=author,
                    back_blurb=blurb,
                    out_path=wrap_path,
                    page_count=request_page_count,
                    page_size=print_size,
                    bleed_inches=bleed,
                    paper=paper,
                    hero_image_path=hero_img,
                    thumbnail_paths=thumbnails,
                    age_range=age_range,
                    title_in_artwork=hero_img is not None and hero_img.exists(),
                )
                out.append(
                    PackageOutput(format="print", language=lang, marketplace=marketplace,
                                 path=str(wrap_path), file_size=wrap_path.stat().st_size)
                )

                # KDP cover: a single PDF containing back + spine + front.
                cover_pdf_path = (
                    packages_dir / "print" / lang
                    / f"{plan.pack_id}-{request_theme}-{marketplace}-cover.pdf"
                )
                build_cover_pdf(wrap_path, cover_pdf_path)
                out.append(
                    PackageOutput(format="print", language=lang, marketplace=marketplace,
                                  path=str(cover_pdf_path), file_size=cover_pdf_path.stat().st_size)
                )

                preview_path = (
                    packages_dir / "print" / lang
                    / f"{plan.pack_id}-{request_theme}-{marketplace}-full-preview.pdf"
                )
                build_full_preview_pdf(cover_pdf_path, interior_path, preview_path)
                out.append(
                    PackageOutput(format="print", language=lang, marketplace=marketplace,
                                  path=str(preview_path), file_size=preview_path.stat().st_size)
                )

    # Digital PDFs are customer-facing products too. When a print bundle is
    # requested, prepend its complete wrap cover so the final PDF is useful as
    # a preview and still contains the requested back cover.
    if "digital" in formats and "print" in formats:
        print_marketplace = pack.profile.formats.print.marketplaces[0]
        for lang in languages:
            cover_pdf = (
                packages_dir / "print" / lang
                / f"{plan.pack_id}-{request_theme}-{print_marketplace}-cover.pdf"
            )
            if not cover_pdf.exists():
                raise RuntimeError(f"digital cover source missing for language {lang}: {cover_pdf}")
            for package in out:
                if (
                    package.format != "digital"
                    or package.language != lang
                    or not package.path.endswith(".pdf")
                ):
                    continue
                digital_pdf = Path(package.path)
                build_full_preview_pdf(cover_pdf, digital_pdf, digital_pdf)
                package.file_size = digital_pdf.stat().st_size

    return PackagePlan(packages=out)


class PackageStage(Stage):
    stage_name = "package"
    inputs = ["concept", "assets"]
    outputs = ["packages"]
    input_models = {"concept": ProductPlan, "assets": AssetPlan}
    prompt_version = PROMPT_VERSION

    def output_files(self, ctx: StageContext) -> list[Path]:
        files: list[Path] = []
        for p in (ctx.packages_dir / "digital").rglob("*"):
            if p.is_file():
                files.append(p)
        for p in (ctx.packages_dir / "print").rglob("*"):
            if p.is_file():
                files.append(p)
        return sorted(files)

    def run(self, ctx: StageContext, concept: ProductPlan, assets: AssetPlan) -> PackagePlan:
        return build_packages(
            assets=assets,
            plan=concept,
            pack=ctx.pack,
            request_theme=ctx.request.theme,
            request_story_id=ctx.request.story_id,
            request_page_count=ctx.request.page_count,
            processed_dir=ctx.processed_dir,
            packages_dir=ctx.packages_dir,
            assets_dir=ctx.assets_dir,
            languages=ctx.request.languages,
            formats=ctx.request.formats,
        )
