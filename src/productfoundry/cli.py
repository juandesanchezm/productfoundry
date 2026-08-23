"""productfoundry CLI."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import typer

from productfoundry.catalog import (
    CatalogError,
    edition_dir_for,
    resolve_book,
)
from productfoundry.engine.pipeline import PIPELINE_ORDER, PipelineExecutor, Stage
from productfoundry.engine.state import ProductState
from productfoundry.pack_loader import PackError, load_pack
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
from productfoundry.stages.release import APPROVAL_MARKER, ReleaseStage
from productfoundry.stages.review import ReviewStage


def _load_env() -> None:
    """Load .env from the project root (repo root or cwd) if present."""
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env"):
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)


_load_env()


app = typer.Typer(no_args_is_help=True)
pack_app = typer.Typer(no_args_is_help=True)
app.add_typer(pack_app, name="pack", help="Manage product packs.")


def _packs_dir() -> Path:
    return Path(os.getenv("PRODUCTFOUNDRY_PACKS_DIR", "packs"))


def _projects_dir() -> Path:
    """Legacy (non-franchise) product outputs. Kept for old workflows only."""
    return Path(os.getenv("PRODUCTFOUNDRY_PROJECTS_DIR", "projects/legacy"))


def _franchises_dir() -> Path:
    return Path(os.getenv("PRODUCTFOUNDRY_FRANCHISES_DIR", "projects"))


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "product"


def _product_dir(franchise: str, series: str, book: str, pack: str) -> Path:
    return edition_dir_for(_franchises_dir() / franchise, series, book, pack)


def _build_stages() -> list[Stage]:
    return [
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
        ReleaseStage(),
    ]


def _print_summary(state: ProductState, project_dir: Path) -> None:
    total = 0.0
    for name in PIPELINE_ORDER:
        node = state.nodes.get(name)
        if node is None:
            continue
        total += node.cost
        flag = ""
        if node.status == "failed":
            flag = f"  ({node.error})"
        typer.echo(f"{name}: {node.status}  ${node.cost:.2f}{flag}")
    typer.echo(f"total cost: ${total:.2f}")
    outputs = project_dir / "packages"
    if outputs.exists():
        typer.echo(f"packages: {outputs}")


def _validate_product_id(product_id: str) -> str:
    """Reject path traversal and unsafe characters in product IDs."""
    if not product_id or product_id != product_id.strip():
        raise typer.BadParameter("product id must be non-empty and stripped")
    if "/" in product_id or "\\" in product_id or ".." in product_id:
        raise typer.BadParameter("product id must not contain path separators or traversal")
    return product_id


def _invalidate_project(project_dir: Path) -> None:
    """Force re-execution: drop node records and stale artifacts/files so the
    next run regenerates everything from scratch. Also clears the release
    approval marker so regenerated content requires fresh human approval."""
    state_path = project_dir / "product.json"
    if state_path.exists():
        state = ProductState.load(project_dir)
        state.nodes = {}
        state.save(project_dir)
    for sub in ("artifacts", "assets", "processed", "packages", "listings"):
        path = project_dir / sub
        if path.exists():
            shutil.rmtree(path)
    marker = project_dir / ".release_approved"
    if marker.exists():
        marker.unlink()


def _run_product(
    project_dir: Path,
    pack_id: str,
    theme: str,
    page_count: int | None,
    languages: list[str],
    formats: list[str],
    title_hint: str,
    runtime_path: Path | None,
    product_id: str,
    franchise: str = "",
    series: str = "",
    book: str = "",
    story_id: str = "",
    character: str = "",
) -> ProductState:
    if franchise:
        try:
            bundle = resolve_book(_franchises_dir() / franchise, series, book, pack_id)
        except (CatalogError, PackError) as e:
            raise typer.BadParameter(str(e))
        pack = bundle.pack
    else:
        pack_dir = _packs_dir() / pack_id
        try:
            pack = load_pack(pack_dir)
        except PackError as e:
            raise typer.BadParameter(str(e))
    runtime = load_runtime_profile(runtime_path)
    from productfoundry.domain.product import ProductRequest

    request = ProductRequest(
        pack=pack_id,
        theme=theme,
        page_count=page_count if page_count is not None else pack.profile.page_count,
        languages=languages,
        formats=formats,
        title_hint=title_hint,
        story_id=story_id,
        character=character,
        franchise=franchise,
        series=series,
        book=book,
    )
    stages = _build_stages()
    executor = PipelineExecutor(stages)
    return executor.execute(
        project_dir, runtime, pack, request, product_id,
        runtime_path=str(runtime_path) if runtime_path else "",
    )


@pack_app.command("validate")
def pack_validate(pack_id: str = typer.Argument(...)) -> None:
    """Validate a pack."""
    try:
        load_pack(_packs_dir() / pack_id)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo("ok")


@app.command("validate")
def franchise_validate(franchise: str = typer.Argument(...)) -> None:
    """Validate a franchise catalog: characters, packs, series, books and
    character contracts (definition hashes and canonical references)."""
    from productfoundry.catalog import load_franchise
    from productfoundry.domain.bible import (
        build_character_bible,
        validate_character_bible,
        validate_story_characters,
    )
    from productfoundry.series import validate_series_contract

    try:
        catalog = load_franchise(_franchises_dir() / franchise)
        errors: list[str] = []
        for series_id, series in catalog.series.items():
            for book in series.books:
                from productfoundry.catalog import resolve_book

                bundle = resolve_book(_franchises_dir() / franchise, series_id, book.id, next(iter(catalog.packs)))
                pack = bundle.pack
                errors.extend(validate_series_contract(pack))
                bible = build_character_bible(pack)
                errors.extend(validate_character_bible(bible))
                errors.extend(validate_story_characters(pack, bible))
                arc = book.data.get("arc", [])
                pages = int(book.data.get("pages", 0))
                if pages and len(arc) != pages:
                    errors.append(
                        f"book {book.id!r}: arc has {len(arc)} beats but declares {pages} pages"
                    )
        if errors:
            for error in errors:
                typer.echo(f"error: {error}", err=True)
            raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo("ok")


@pack_app.command("create")
def pack_create(pack_id: str = typer.Argument(...)) -> None:
    """Create a pack by copying the example template."""
    source = _packs_dir() / "example"
    target = _packs_dir() / pack_id
    if not source.exists():
        typer.echo(
            f"error: template pack not found at {source}. Create one manually first.",
            err=True,
        )
        raise typer.Exit(1)
    if target.exists():
        typer.echo(f"error: pack {pack_id!r} already exists", err=True)
        raise typer.Exit(1)
    shutil.copytree(source, target)
    typer.echo(f"created {target}")


@app.command()
def create(
    pack: str = typer.Option(..., "--pack", help="Pack id"),
    theme: str = typer.Option(..., "--theme", help="Theme/sub-niche or story id"),
    pages: int | None = typer.Option(None, "--pages", help="Number of pages to generate (defaults to the pack)"),
    languages: str = typer.Option("en,es", "--languages", help="Comma-separated languages"),
    formats: str = typer.Option("digital,print", "--formats", help="Comma-separated formats"),
    title_hint: str = typer.Option("", "--title-hint", help="Optional title hint"),
    runtime: Path | None = typer.Option(None, "--runtime", help="Path to runtime yaml"),
    product_id: str | None = typer.Option(None, "--product-id", help="Project id"),
    force: bool = typer.Option(False, "--force", help="Re-run an existing product"),
    no_audit: bool = typer.Option(False, "--no-audit", help="Skip the audit gate (faster, no LLM judge)"),
    story: str = typer.Option("", "--story", help="Story id from pack.stories.yaml"),
    character: str = typer.Option("", "--character", help="Protagonist descriptor for character consistency"),
    franchise: str = typer.Option("", "--franchise", help="Franchise directory (e.g. cocholate)"),
    series: str = typer.Option("", "--series", help="Series id inside the franchise"),
    book: str = typer.Option("", "--book", help="Book id inside the series"),
) -> None:
    """Run a full product pipeline."""
    lang_list = [l.strip() for l in languages.split(",") if l.strip()]
    format_list = [f.strip() for f in formats.split(",") if f.strip()]
    if no_audit:
        os.environ["PRODUCTFOUNDRY_SKIP_AUDIT"] = "1"
    if franchise:
        if not series or not book:
            raise typer.BadParameter("--series and --book are required with --franchise")
        for value in (series, book):
            _validate_product_id(value)
        pid = product_id or _slugify(f"{book}-{pack}")
        project_dir = _product_dir(franchise, series, book, pack)
    else:
        pid = product_id or _slugify(f"{pack}-{theme}")
        project_dir = _projects_dir() / pid
    _validate_product_id(pid)
    if project_dir.exists() and not force:
        typer.echo(
            f"error: product {pid!r} exists, use `productfoundry resume {pid}`",
            err=True,
        )
        raise typer.Exit(1)
    if force and project_dir.exists():
        _invalidate_project(project_dir)
    try:
        state = _run_product(
            project_dir, pack, theme, pages, lang_list, format_list, title_hint, runtime, pid,
            franchise=franchise, series=series, book=book,
            story_id=story or book, character=character or "",
        )
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    _print_summary(state, project_dir)


@app.command()
def resume(
    product_id: str = typer.Argument(...),
    franchise: str = typer.Option("", "--franchise", help="Franchise directory"),
    runtime: Path | None = typer.Option(None, "--runtime", help="Path to runtime yaml"),
    start_at: str = typer.Option("", "--start-at", help="Stage name to start from (re-runs it and everything after)"),
) -> None:
    """Re-execute pending or invalidated nodes of an existing product.

    With --start-at the pipeline skips every node before that stage and
    re-runs the named stage plus all downstream nodes. Useful to regenerate
    only the cover/back-cover/package outputs without touching interior
    pages (e.g. `--start-at hero`).
    """
    _validate_product_id(product_id)
    try:
        project_dir = _resolve_project_dir(franchise, product_id)
        state = ProductState.load(project_dir)
        from productfoundry.domain.product import ProductRequest

        request = ProductRequest.model_validate(state.request)
        # Use the persisted runtime_path when no explicit --runtime is given,
        # so resume does not silently switch providers/models.
        effective_runtime = runtime
        if effective_runtime is None and state.runtime_path:
            effective_runtime = Path(state.runtime_path)
        if start_at:
            if start_at not in PIPELINE_ORDER:
                raise typer.BadParameter(
                    f"unknown stage {start_at!r} (available: {', '.join(PIPELINE_ORDER)})"
                )
            from productfoundry.engine.pipeline import start_from_stage

            start_from_stage(state, start_at)
        state = _run_product(
            project_dir,
            request.pack,
            request.theme,
            request.page_count,
            request.languages,
            request.formats,
            request.title_hint,
            effective_runtime,
            product_id,
            franchise=franchise or request.franchise,
            series=request.series,
            book=request.book,
            story_id=request.story_id,
            character=request.character,
        )
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    _print_summary(state, project_dir)


def _find_edition_state(franchise: str, product_id: str) -> dict | None:
    """Locate an edition's product.json by its product_id inside a franchise."""
    franchises_dir = _franchises_dir()
    if not franchises_dir.exists():
        return None
    for state_file in sorted((franchises_dir / franchise).glob("series/*/books/*/editions/*/product.json")):
        state = ProductState.load(state_file.parent)
        if state.product_id == product_id:
            return {"dir": state_file.parent, "state": state}
    return None


def _resolve_project_dir(franchise: str, product_id: str) -> Path:
    """Resolve an edition dir from (franchise, product_id), or the legacy dir."""
    if franchise:
        catalog_state = _find_edition_state(franchise, product_id)
        if not catalog_state:
            raise typer.BadParameter(f"product {product_id!r} not found in franchise {franchise!r}")
        return catalog_state["dir"]
    return _projects_dir() / product_id


@app.command()
def status(
    product_id: str = typer.Argument(...),
    franchise: str = typer.Option("", "--franchise", help="Franchise directory"),
) -> None:
    """Show the node table of a product."""
    _validate_product_id(product_id)
    try:
        project_dir = _resolve_project_dir(franchise, product_id)
        state = ProductState.load(project_dir)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{'stage':<14} {'status':<8} {'attempts':>8} {'input_hash':<12} {'cost':>8}")
    for name in PIPELINE_ORDER:
        node = state.nodes.get(name)
        if node is None:
            continue
        short_hash = node.input_hash[:11] if node.input_hash else ""
        typer.echo(f"{name:<14} {node.status:<8} {node.attempts:>8} {short_hash:<12} {node.cost:>8.2f}")


@app.command()
def release(
    product_id: str = typer.Argument(...),
    franchise: str = typer.Option("", "--franchise", help="Franchise directory"),
    approve: bool = typer.Option(False, "--approve", help="Explicit human approval to publish"),
) -> None:
    """Final release gate. Without --approve the product is never publishable."""
    _validate_product_id(product_id)
    project_dir = _resolve_project_dir(franchise, product_id)
    if not (project_dir / "product.json").exists():
        typer.echo(f"error: product {product_id!r} not found", err=True)
        raise typer.Exit(1)
    if approve:
        (project_dir / APPROVAL_MARKER).write_text("approved")
        typer.echo("human approval recorded; re-running release gate")
    else:
        marker = project_dir / APPROVAL_MARKER
        if marker.exists():
            marker.unlink()
        typer.echo("approval cleared; re-running release gate")
    try:
        state = ProductState.load(project_dir)
        from productfoundry.domain.product import ProductRequest

        request = ProductRequest.model_validate(state.request)
        state = _run_product(
            project_dir,
            request.pack,
            request.theme,
            request.page_count,
            request.languages,
            request.formats,
            request.title_hint,
            Path(state.runtime_path) if state.runtime_path else None,
            product_id,
            franchise=franchise or request.franchise,
            series=request.series,
            book=request.book,
            story_id=request.story_id,
            character=request.character,
        )
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    _print_summary(state, project_dir)
    manifest_path = project_dir / "publication-manifest.json"
    if manifest_path.exists():
        import json

        manifest = json.loads(manifest_path.read_text())
        typer.echo(f"publishable: {manifest.get('publishable', False)}")


@app.command("list")
def list_products() -> None:
    """List products with pack and latest status."""
    rows = []
    total = 0.0
    franchises_dir = _franchises_dir()
    if franchises_dir.exists():
        for franchise_dir in sorted(franchises_dir.iterdir()):
            if not franchise_dir.is_dir():
                continue
            for state_file in sorted(franchise_dir.glob("series/*/books/*/editions/*/product.json")):
                state = ProductState.load(state_file.parent)
                latest = "n/a"
                for name in reversed(PIPELINE_ORDER):
                    node = state.nodes.get(name)
                    if node is not None:
                        latest = node.status
                        break
                total += state.total_cost()
                rows.append((state.product_id, state.pack_id, latest))
    projects_dir = _projects_dir()
    if projects_dir.exists():
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            state_file = project_dir / "product.json"
            if not state_file.exists():
                continue
            state = ProductState.load(project_dir)
            latest = "n/a"
            for name in reversed(PIPELINE_ORDER):
                node = state.nodes.get(name)
                if node is not None:
                    latest = node.status
                    break
            total += state.total_cost()
            rows.append((state.product_id, state.pack_id, latest))
    if not rows:
        typer.echo("no products")
        return
    for pid, pack_id, status_ in rows:
        typer.echo(f"{pid:<30} pack={pack_id:<18} status={status_}")
    typer.echo(f"total cost: ${total:.2f}")
