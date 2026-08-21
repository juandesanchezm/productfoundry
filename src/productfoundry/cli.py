"""productfoundry CLI."""
from __future__ import annotations
import os
import re
import shutil
from pathlib import Path

import typer

from productfoundry.engine.pipeline import PIPELINE_ORDER, PipelineExecutor, Stage
from productfoundry.engine.state import ProductState
from productfoundry.pack_loader import PackError, load_pack
from productfoundry.runtime import load_runtime_profile
from productfoundry.stages.assets import AssetsStage
from productfoundry.stages.concept import ConceptStage
from productfoundry.stages.listing import ListingStage
from productfoundry.stages.package import PackageStage
from productfoundry.stages.postprocess import PostprocessStage
from productfoundry.stages.review import ReviewStage


app = typer.Typer(no_args_is_help=True)
pack_app = typer.Typer(no_args_is_help=True)
app.add_typer(pack_app, name="pack", help="Manage product packs.")


def _packs_dir() -> Path:
    return Path(os.getenv("PRODUCTFOUNDRY_PACKS_DIR", "packs"))


def _projects_dir() -> Path:
    return Path(os.getenv("PRODUCTFOUNDRY_PROJECTS_DIR", "projects"))


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "product"


def _build_stages() -> list[Stage]:
    return [
        ConceptStage(),
        AssetsStage(),
        PostprocessStage(),
        PackageStage(),
        ListingStage(),
        ReviewStage(),
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


def _run_product(
    project_dir: Path,
    pack_id: str,
    theme: str,
    page_count: int,
    languages: list[str],
    formats: list[str],
    title_hint: str,
    runtime_path: Path | None,
    product_id: str,
) -> ProductState:
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
        page_count=page_count,
        languages=languages,
        formats=formats,
        title_hint=title_hint,
    )
    stages = _build_stages()
    executor = PipelineExecutor(stages)
    return executor.execute(project_dir, runtime, pack, request, product_id)


@pack_app.command("validate")
def pack_validate(pack_id: str = typer.Argument(...)) -> None:
    """Validate a pack."""
    try:
        load_pack(_packs_dir() / pack_id)
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
    theme: str = typer.Option(..., "--theme", help="Theme/sub-niche"),
    pages: int = typer.Option(30, "--pages", help="Number of pages to generate"),
    languages: str = typer.Option("en,es", "--languages", help="Comma-separated languages"),
    formats: str = typer.Option("digital,print", "--formats", help="Comma-separated formats"),
    title_hint: str = typer.Option("", "--title-hint", help="Optional title hint"),
    runtime: Path | None = typer.Option(None, "--runtime", help="Path to runtime yaml"),
    product_id: str | None = typer.Option(None, "--product-id", help="Project id"),
    force: bool = typer.Option(False, "--force", help="Re-run an existing product"),
) -> None:
    """Run a full product pipeline."""
    lang_list = [l.strip() for l in languages.split(",") if l.strip()]
    format_list = [f.strip() for f in formats.split(",") if f.strip()]
    pid = product_id or _slugify(f"{pack}-{theme}")
    project_dir = _projects_dir() / pid
    if project_dir.exists() and not force:
        typer.echo(
            f"error: product {pid!r} exists, use `productfoundry resume {pid}`",
            err=True,
        )
        raise typer.Exit(1)
    try:
        state = _run_product(
            project_dir, pack, theme, pages, lang_list, format_list, title_hint, runtime, pid
        )
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    _print_summary(state, project_dir)


@app.command()
def resume(
    product_id: str = typer.Argument(...),
    runtime: Path | None = typer.Option(None, "--runtime", help="Path to runtime yaml"),
) -> None:
    """Re-execute pending or invalidated nodes of an existing product."""
    project_dir = _projects_dir() / product_id
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
            runtime,
            product_id,
        )
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    _print_summary(state, project_dir)


@app.command()
def status(product_id: str = typer.Argument(...)) -> None:
    """Show the node table of a product."""
    project_dir = _projects_dir() / product_id
    try:
        state = ProductState.load(project_dir)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{'stage':<14} {'status':<8} {'input_hash':<12} {'cost':>8}")
    for name in PIPELINE_ORDER:
        node = state.nodes.get(name)
        if node is None:
            continue
        short_hash = node.input_hash[:11] if node.input_hash else ""
        typer.echo(f"{name:<14} {node.status:<8} {short_hash:<12} {node.cost:>8.2f}")


@app.command("list")
def list_products() -> None:
    """List products with pack and latest status."""
    projects_dir = _projects_dir()
    if not projects_dir.exists():
        typer.echo("no products")
        return
    rows = []
    total = 0.0
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
    for pid, pack_id, status_ in rows:
        typer.echo(f"{pid:<30} pack={pack_id:<18} status={status_}")
    typer.echo(f"total cost: ${total:.2f}")
