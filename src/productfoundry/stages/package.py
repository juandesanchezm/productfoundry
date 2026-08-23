"""package stage — deterministic build of digital + print deliverables across languages."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import ClassVar

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
from productfoundry.stages.story_helpers import localized_story_subtitle

PROMPT_VERSION = "package-v4"


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
    digital_safe = float(spec.get("inner_safe_inches", 0.25))
    if "digital" in formats:
        fs = pack.profile.formats.digital
        for marketplace in fs.marketplaces:
            for lang in languages:
                pdf_path = (
                    packages_dir / "digital" / lang
                    / f"{plan.pack_id}-{request_theme}-{marketplace}.pdf"
                )
                build_pdf(
                    image_paths, pdf_path,
                    page_size=digital_size, bleed=0.0, inner_safe_inches=digital_safe,
                )
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
        print_size = spec.get("page_size", "8.5x11")
        bleed = float(spec.get("bleed_inches", 0.0))
        safe = float(spec.get("inner_safe_inches", 0.375))
        paper = spec.get("paper", "white")
        cover_spec = _packaging_spec(pack, "cover")
        cover_bleed = float(cover_spec.get("bleed_inches", 0.125))
        author = _get_author(pack)
        story = _lookup_story(pack, request_story_id)

        for marketplace in ps.marketplaces:
            for lang in languages:
                # Interior PDF: no-bleed interior for line-art pages.
                # The page size equals the trim size and the ink-safe margin
                # (0.375in inside/outside for 24-150 pages) stays white.
                interior_path = (
                    packages_dir / "print" / lang
                    / f"{plan.pack_id}-{request_theme}-{marketplace}-interior.pdf"
                )
                build_pdf(
                    image_paths, interior_path,
                    page_size=print_size, bleed=bleed, inner_safe_inches=safe,
                )
                out.append(
                    PackageOutput(format="print", language=lang, marketplace=marketplace,
                                 path=str(interior_path), file_size=interior_path.stat().st_size)
                )

                # Wrap cover (front + spine + back) with KDP-compliant dimensions.
                # The single English hero artwork (copy embedded by the image
                # model) is shared by every language output.
                wrap_path = (
                    packages_dir / "print" / lang
                    / f"{plan.pack_id}-{request_theme}-{marketplace}-cover.png"
                )
                title = plan.titles.get(lang, plan.titles.get("en", request_theme))
                subtitle = localized_story_subtitle(pack, request_story_id, lang, plan.subtitle)
                blurb = _get_description_blurb(pack, plan, lang, story)
                hero_img = assets_dir / "cover_hero.png"
                if not hero_img.exists():
                    hero_img = assets_dir / "cover_hero_en.png" if (assets_dir / "cover_hero_en.png").exists() else (image_paths[0] if image_paths else None)
                # Interior-page thumbnails for the back cover (bestseller convention)
                thumbnails = [image_paths[i] for i in range(6) if i < len(image_paths)]
                age_range = getattr(pack.profile, "age_range", "") or ""
                back_img = assets_dir / "back_cover.png"
                if not back_img.exists():
                    back_img = assets_dir / "back_cover_en.png"
                build_wrap_cover(
                    title=title,
                    subtitle=subtitle,
                    author=author,
                    back_blurb=blurb,
                    out_path=wrap_path,
                    page_count=request_page_count,
                    page_size=print_size,
                    bleed_inches=cover_bleed,
                    paper=paper,
                    hero_image_path=hero_img,
                    back_image_path=back_img if back_img.exists() else None,
                    thumbnail_paths=thumbnails,
                    age_range=age_range,
                    language=lang,
                    title_in_artwork=True,
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

    _build_kdp_upload_kit(
        packages_dir=packages_dir,
        languages=languages,
        print_marketplaces=(
            pack.profile.formats.print.marketplaces if "print" in formats else []
        ),
        plan=plan,
        request_theme=request_theme,
    )

    return PackagePlan(packages=out)


def _build_kdp_upload_kit(
    packages_dir: Path,
    languages: list[str],
    print_marketplaces: list[str],
    plan: ProductPlan,
    request_theme: str,
) -> None:
    """Assemble the manual KDP upload kit per language.

    KDP needs exactly two files per paperback: the interior manuscript PDF
    (trim size, no bleed, 300 DPI) and the cover PDF (one continuous wrap
    with 0.125in bleed). The kit copies them into `packages/kdp_upload/<lang>/`
    together with a checklist, so a human can upload them without hunting
    through the build tree.
    """
    if not print_marketplaces:
        return
    marketplace = print_marketplaces[0]
    for lang in languages:
        interior = packages_dir / "print" / lang / f"{plan.pack_id}-{request_theme}-{marketplace}-interior.pdf"
        cover = packages_dir / "print" / lang / f"{plan.pack_id}-{request_theme}-{marketplace}-cover.pdf"
        if not interior.exists() or not cover.exists():
            continue
        kit = packages_dir / "kdp_upload" / lang
        kit.mkdir(parents=True, exist_ok=True)
        kit_interior = kit / f"{plan.pack_id}-{request_theme}-interior.pdf"
        kit_cover = kit / f"{plan.pack_id}-{request_theme}-cover.pdf"
        shutil.copyfile(interior, kit_interior)
        shutil.copyfile(cover, kit_cover)
        (kit / "kdp-checklist.md").write_text(
            f"""# KDP upload checklist ({lang})

1. Create a new paperback (or update) on the KDP dashboard with:
   - Title: {plan.titles.get(lang, plan.titles.get('en', request_theme))}
   - Trim size: 8.5 x 11 in
   - Interior: white paper, black ink (line art)
   - Bleed: no bleed (line-art pages keep a white safe margin)
2. Upload `{kit_interior.name}` as the manuscript (trim-size PDF, 300 DPI).
3. Upload `{kit_cover.name}` as the cover (wrap: back + spine + front,
   0.125in bleed, 300 DPI). KDP places the ISBN barcode automatically.
4. Verify page count is {len(plan.pages)} interior pages and preview every page.
5. Confirm the AI content disclosure matches the pack compliance config.
6. Order a proof copy before publishing.
""",
            encoding="utf-8",
        )


class PackageStage(Stage):
    stage_name = "package"
    inputs: ClassVar = ["concept", "assets"]
    outputs: ClassVar = ["packages"]
    input_models: ClassVar = {"concept": ProductPlan, "assets": AssetPlan}
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
