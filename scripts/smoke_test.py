"""Smoke test: end-to-end pipeline run with placeholder providers.

Reuses an existing edition's assets (interior pages, character sheets) so the
test only exercises postprocess→release stages, while still verifying cover
regeneration, packages, listings and manifest. Deterministic, no API cost.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from productfoundry.catalog import resolve_book
from productfoundry.domain.product import ProductRequest
from productfoundry.engine.pipeline import PIPELINE_ORDER, PipelineExecutor
from productfoundry.runtime import load_runtime_profile
from productfoundry.stages.assets import AssetsStage
from productfoundry.stages.audit import (
    AssetAuditStage,
    CharacterSheetAuditStage,
    PromptAuditStage,
)
from productfoundry.stages.back_cover import BackCoverStage
from productfoundry.stages.character_sheet import CharacterSheetStage
from productfoundry.stages.concept import ConceptStage
from productfoundry.stages.hero import HeroStage
from productfoundry.stages.lineart_check import LineArtCheckStage
from productfoundry.stages.listing import ListingStage
from productfoundry.stages.pack_validate import PackValidationStage
from productfoundry.stages.package import PackageStage
from productfoundry.stages.postprocess import PostprocessStage
from productfoundry.stages.printcheck import PrintCheckStage
from productfoundry.stages.release import ReleaseStage  # noqa: F401  (kept for completeness)
from productfoundry.stages.review import ReviewStage


def main() -> int:
    src = Path("projects/cocholate/series/cocholate-adventures/books/magical-day/editions/coloring-fantasy")
    tmp = Path("/tmp/smoke_coloring_fantasy")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src, tmp)
    for stale in (".pipeline.lock", "product.json", "publication-manifest.json"):
        p = tmp / stale
        if p.exists():
            p.unlink()
    # Drop pre-existing listings and packages so we observe a clean rebuild.
    for sub in (tmp / "listings", tmp / "packages"):
        if sub.exists():
            shutil.rmtree(sub)

    runtime = load_runtime_profile(Path("runtime/smoke.yaml"))
    bundle = resolve_book(
        Path("projects/cocholate"), "cocholate-adventures", "magical-day", "coloring-fantasy"
    )
    request = ProductRequest.model_validate(
        {
            "pack": "coloring-fantasy",
            "theme": "magical-day",
            "page_count": 24,
            "languages": ["en", "es"],
            "formats": ["digital", "print"],
            "title_hint": "",
            "story_id": "magical-day",
            "character": "",
            "franchise": "cocholate",
            "series": "cocholate-adventures",
            "book": "magical-day",
        }
    )

    stages = [
        ConceptStage(),
        PromptAuditStage(),
        PackValidationStage(),
        CharacterSheetStage(),
        CharacterSheetAuditStage(),
        AssetsStage(),
        AssetAuditStage(),
        PostprocessStage(),
        LineArtCheckStage(),
        HeroStage(),
        BackCoverStage(),
        PackageStage(),
        PrintCheckStage(),
        ListingStage(),
        ReviewStage(),
        # ReleaseStage is skipped on purpose: it gates on a human approval
        # marker that has not been written. The smoke test focuses on the
        # production pipeline; release is exercised separately by
        # `productfoundry release --approve`.
    ]
    executor = PipelineExecutor(stages)
    state = executor.execute(
        tmp, runtime, bundle.pack, request, "magical-day-coloring-fantasy", runtime_path="runtime/smoke.yaml"
    )

    print("\n== node results ==")
    for name in PIPELINE_ORDER:
        node = state.nodes.get(name)
        if not node:
            continue
        print(f"{name:>22}: {node.status:>8} cost=${node.cost:.4f}")

    print(f"\ntotal cost: ${state.total_cost():.4f}")

    print("\n== output presence ==")
    for lang in ("en", "es"):
        hero = tmp / "assets" / f"cover_hero_{lang}.png"
        back = tmp / "assets" / f"back_cover_{lang}.png"
        print(f"  hero {lang}: {'ok' if hero.exists() else 'MISSING'} ({hero.stat().st_size if hero.exists() else 0} B)")
        print(f"  back {lang}: {'ok' if back.exists() else 'MISSING'} ({back.stat().st_size if back.exists() else 0} B)")

    for fmt in ("digital", "print"):
        lang_dir = tmp / "packages" / fmt / "en"
        if lang_dir.exists():
            files = sorted(p.name for p in lang_dir.glob("*"))
            print(f"  packages/{fmt}/en: {len(files)} -> {files[:4]}{'...' if len(files) > 4 else ''}")

    kdp_dir = tmp / "packages" / "kdp_upload" / "en"
    if kdp_dir.exists():
        files = sorted(p.name for p in kdp_dir.glob("*"))
        print(f"  packages/kdp_upload/en: {files}")

    listings = sorted((tmp / "listings").glob("*.json"))
    print(f"\nlistings ({len(listings)}):")
    for p in listings:
        print(f"  {p.name}")

    manifest = tmp / "publication-manifest.json"
    print(f"\npublication-manifest: {'ok' if manifest.exists() else 'MISSING'}")
    if manifest.exists():
        import json

        data = json.loads(manifest.read_text())
        files = data.get("deliverables", [])
        print(f"  deliverables: {len(files)}")
        for f in files[:6]:
            print(f"    - {f.get('path')} sha256={f.get('sha256', '')[:12]}...")

    all_done = all(state.nodes[n].status == "done" for n in PIPELINE_ORDER if n in state.nodes)
    release_node = state.nodes.get("release")
    print(f"\nrelease verdict: {release_node.error if release_node else '?'}")
    return 0 if all_done else 1


if __name__ == "__main__":
    raise SystemExit(main())
