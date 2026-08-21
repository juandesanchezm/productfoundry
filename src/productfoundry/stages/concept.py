"""concept stage — expand pack themes into a ProductPlan with per-page prompts and titles."""
from __future__ import annotations
from pydantic import BaseModel

from productfoundry.domain.pack import PackProfile
from productfoundry.domain.product import PageSpec, ProductPlan, ProductRequest
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.stages.helpers import estimate_cost, retry_parse


PROMPT_VERSION = "concept-v1"
_SYSTEM = "Eres un generador de planes de producto para packs digitales. Genera SOLO JSON."


class ConceptSchema(BaseModel):
    pages: list[dict] = []
    titles: dict[str, str] = {}
    subtitle: str = ""
    description_hint: str = ""


def _build_prompt(pack: PackProfile, request: ProductRequest) -> str:
    style = pack.pack_type
    lang_str = ", ".join(pack.languages)
    return f"""Genera un plan de un producto digital.

Pack: {pack.id}
Tipo: {style}
Tema: {request.theme}
Número de páginas: {request.page_count}
Idiomas: {lang_str}
Pista de título: {request.title_hint or "(ninguna)"}

Devuelve SOLO JSON con esta estructura:
{{
  "pages": [
    {{"id": "page_001", "index": 1, "prompt": "<prompt detallado en inglés para generar imagen>", "title": "<título corto>"}}
  ],
  "titles": {{"en": "<título en inglés>", "es": "<título en español>"}},
  "subtitle": "<subtítulo corto>",
  "description_hint": "<1 frase descriptiva>"
}}

Las páginas deben ser variadas dentro del tema ({request.theme}). El prompt debe estar optimizado para generar imágenes tipo {style} imprimibles, con líneas claras y mucho espacio en blanco.
"""


def _slug(pack_id: str, theme: str) -> str:
    import re

    base = f"{pack_id}-{theme}"
    return re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-")


class ConceptStage(Stage):
    stage_name = "concept"
    inputs = []
    outputs = ["concept"]
    prompt_version = PROMPT_VERSION

    def run(self, ctx: StageContext, **inputs: BaseModel) -> ProductPlan:
        prompt = _build_prompt(ctx.pack.profile, ctx.request)
        result = retry_parse(
            ctx.llm,
            _SYSTEM,
            prompt,
            ConceptSchema,
            on_response=lambda r: ctx.set_cost(estimate_cost(r)),
        )

        pages = [
            PageSpec(
                id=p.get("id", f"page_{i + 1:03d}"),
                index=p.get("index", i + 1),
                prompt=p.get("prompt", ""),
                title=p.get("title", p.get("id", f"page_{i + 1:03d}")),
                theme=ctx.request.theme,
            )
            for i, p in enumerate(result.pages)
        ]

        return ProductPlan(
            pack_id=ctx.pack.profile.id,
            pack_version=ctx.pack.profile.pack_version,
            theme=ctx.request.theme,
            pages=pages,
            titles=result.titles,
            subtitle=result.subtitle,
            description_hint=result.description_hint,
        )
