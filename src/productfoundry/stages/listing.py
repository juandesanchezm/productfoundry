"""listing stage — generate SEO titles, descriptions, tags per marketplace and language."""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from productfoundry.domain.listing import Listing, ListingSet
from productfoundry.domain.packaging import PackagePlan
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.stages.helpers import estimate_cost, retry_parse
from productfoundry.stages.story_helpers import localized_series_name, localized_story_subtitle

PROMPT_VERSION = "listing-v3"
_SYSTEM = "Eres un experto en SEO para marketplaces digitales. Genera SOLO JSON."


class ListingSchema(BaseModel):
    listings: list[dict] = []


def normalize_listing(data: dict) -> Listing:
    """Apply deterministic marketplace constraints after model generation."""
    marketplace = str(data.get("marketplace", ""))
    tags = [str(tag).strip() for tag in data.get("tags", []) if str(tag).strip()]
    if marketplace.lower() == "etsy":
        tags = tags[:13]
    return Listing(
        marketplace=marketplace,
        language=str(data.get("language", "en")),
        title=str(data.get("title", "")),
        description=str(data.get("description", "")),
        tags=tags,
        price=float(data.get("price", 0.0)),
        category=str(data.get("category", "")),
        ai_disclosure=(
            str(data.get("ai_disclosure", "")).strip()
            or (
                "This listing contains AI-assisted artwork generated from original prompts "
                "and reviewed by the seller."
            )
        ),
    )


def _build_prompt(
    pack,
    plan: ProductPlan,
    languages: list[str],
    formats: list[str],
    story_id: str = "",
) -> str:
    profile = getattr(pack, "profile", pack)
    series_line = ""
    subtitle_lines = ""
    for lang in languages:
        series = localized_series_name(pack, lang)
        if series:
            series_line += f"\nSerie ({lang}): {series}"
        subtitle = localized_story_subtitle(pack, story_id, lang, plan.subtitle)
        if subtitle:
            subtitle_lines += f"\nSubtítulo ({lang}): {subtitle}"
    return f"""Genera listings SEO para este producto.

Pack: {profile.id} ({profile.pack_type})
Tema: {plan.theme}
Páginas: {len(plan.pages)}
Idiomas: {", ".join(languages)}
Formatos: {", ".join(formats)}
Título (en): {plan.titles.get("en", "")}
Título (es): {plan.titles.get("es", "")}{subtitle_lines}
Pista: {plan.description_hint}{series_line}

    Genera un listing por cada combinación de formato, marketplace e idioma.
    Crea entradas distintas para digital y print cuando aplique.
    No menciones números de volumen ni numeración en los títulos.

Devuelve SOLO JSON:
{{
  "listings": [
    {{
      "marketplace": "<marketplace id>",
      "language": "<language code>",
      "format": "digital|print",
      "title": "<título SEO, max 140 chars>",
      "description": "<descripción con markdown, 200-400 palabras>",
      "tags": ["<tag1>", "<tag2>", "..."],
      "price": <precio en USD/EUR, decimal>,
       "category": "<categoría del marketplace>",
       "ai_disclosure": "<disclosure breve cuando el marketplace lo requiere>"
    }}
  ]
}}
"""


class ListingStage(Stage):
    stage_name = "listing"
    inputs: ClassVar = ["concept", "packages"]
    outputs: ClassVar = ["listings"]
    input_models: ClassVar = {"concept": ProductPlan, "packages": PackagePlan}
    prompt_version = PROMPT_VERSION

    def output_files(self, ctx: StageContext) -> list[Path]:
        return sorted(ctx.listings_dir.glob("*.json")) if ctx.listings_dir.exists() else []

    def run(self, ctx: StageContext, concept: ProductPlan, packages: PackagePlan) -> ListingSet:
        prompt = _build_prompt(
            ctx.pack,
            concept,
            ctx.request.languages,
            ctx.request.formats,
            story_id=ctx.request.story_id,
        )
        result = retry_parse(
            ctx.llm,
            _SYSTEM,
            prompt,
            ListingSchema,
            on_response=lambda r: ctx.set_cost(estimate_cost(r)),
        )

        listings = [normalize_listing(raw_listing) for raw_listing in result.listings]

        # Persist a copy to disk for manual upload reference
        ctx.listings_dir.mkdir(parents=True, exist_ok=True)
        for listing in listings:
            out = ctx.listings_dir / f"{listing.marketplace}-{listing.language}.json"
            out.write_text(listing.model_dump_json(indent=2))

        return ListingSet(listings=listings)
