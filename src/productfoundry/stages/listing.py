"""listing stage — generate SEO titles, descriptions, tags per marketplace and language."""
from __future__ import annotations
from pydantic import BaseModel

from productfoundry.domain.listing import Listing, ListingSet
from productfoundry.domain.pack import PackProfile
from productfoundry.domain.packaging import PackagePlan
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.stages.helpers import estimate_cost, retry_parse


PROMPT_VERSION = "listing-v1"
_SYSTEM = "Eres un experto en SEO para marketplaces digitales. Genera SOLO JSON."


class ListingSchema(BaseModel):
    listings: list[dict] = []


def _build_prompt(pack: PackProfile, plan: ProductPlan, languages: list[str], formats: list[str]) -> str:
    return f"""Genera listings SEO para este producto.

Pack: {pack.id} ({pack.pack_type})
Tema: {plan.theme}
Páginas: {len(plan.pages)}
Idiomas: {", ".join(languages)}
Formatos: {", ".join(formats)}
Título (en): {plan.titles.get("en", "")}
Título (es): {plan.titles.get("es", "")}
Subtítulo: {plan.subtitle}
Pista: {plan.description_hint}

Genera un listing por cada combinación de (formato/marketplace, idioma). Una entrada distinta para digital y otra para print cuando aplique.

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
      "category": "<categoría del marketplace>"
    }}
  ]
}}
"""


class ListingStage(Stage):
    stage_name = "listing"
    inputs = ["concept", "packages"]
    outputs = ["listings"]
    input_models = {"concept": ProductPlan, "packages": PackagePlan}
    prompt_version = PROMPT_VERSION

    def run(self, ctx: StageContext, concept: ProductPlan, packages: PackagePlan) -> ListingSet:
        prompt = _build_prompt(ctx.pack.profile, concept, ctx.request.languages, ctx.request.formats)
        result = retry_parse(
            ctx.llm,
            _SYSTEM,
            prompt,
            ListingSchema,
            on_response=lambda r: ctx.set_cost(estimate_cost(r)),
        )

        listings = [
            Listing(
                marketplace=l.get("marketplace", ""),
                language=l.get("language", "en"),
                title=l.get("title", ""),
                description=l.get("description", ""),
                tags=l.get("tags", []),
                price=float(l.get("price", 0.0)),
                category=l.get("category", ""),
            )
            for l in result.listings
        ]

        # Persist a copy to disk for manual upload reference
        ctx.listings_dir.mkdir(parents=True, exist_ok=True)
        for l in listings:
            out = ctx.listings_dir / f"{l.marketplace}-{l.language}.json"
            out.write_text(l.model_dump_json(indent=2))

        return ListingSet(listings=listings)
