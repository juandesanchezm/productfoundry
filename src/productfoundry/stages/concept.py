"""concept stage — expand pack themes into a ProductPlan with per-page prompts and titles."""
from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from productfoundry.domain.bible import normalize_character_ids
from productfoundry.domain.product import PageSpec, ProductPlan, ProductRequest
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.stages.helpers import estimate_cost, retry_parse

PROMPT_VERSION = "concept-v3"
_SYSTEM = "Eres un generador de planes de producto para packs digitales. Genera SOLO JSON."


class ConceptSchema(BaseModel):
    pages: list[dict] = []
    titles: dict[str, str] = {}
    description_hint: str = ""


_COMPOSITION_RULES_V2 = """Reglas de composición (CRÍTICO para reducir errores anatómicos manteniendo riqueza visual):

REQUERIDO:
- 1 figura PROTAGONISTA centrada en el cuadro (cabeca y cuerpo completos visibles, margen de aire arriba y abajo).
- PROTAGONISTA con diseño consistente en TODAS las páginas (misma silueta, rasgos, proporciones).
- ESCENOGRAFÍA de fondo permitida: castillo, prado, bosque de setas, habitación, cueva, lago, etc. Las infraestructuras no glitchean.
- PROPS seguros permitidos: cofre del tesoro, tetera, farolillos, libros, setas, pociones en mesa, etc. NO en manos del protagonista.
- Personajes SECUNDARIOS: solo los definidos en la lista de personajes del pack (bloque PERSONAJES DEL UNIVERSO). Hasta 2 criaturas pequeñas por página, cada una de tamaño <20% del protagonista, diseño fijo y presencia justificada por el beat. Nunca inventes personajes fuera de esa lista.

PROHIBIDO:
- 2ª figura de tamaño comparable (no queremos escenas con dos protagonistas).
- Garras/manos sosteniendo objetos detallados (espadas, escudos, herramientas en mano).
- Rostros de cerca o expresiones complejas (sonríe/neutral, sin gestos ambiguos).
- Perspectiva forzada o poses con extremidades en ángulo.
- Cropping: nada tocando los 4 bordes.

Diseña cada prompt para UNA PÁGINA completa, con el protagonista presente.
"""

_STORY_RULES = """MODO HISTORIA (story mode):
- Las páginas tienen un ARCO narrativo: inicio -> 3-5 beats de desarrollo -> final.
- Cada página es un BEAT del arco (acción presente, no abstracta).
- El MISMO protagonista aparece en TODAS las páginas con el MISMO diseño.
- Conecta los beats con coherencia visual (mismo entorno escala, mismo "estilo del dibujo").
- El título y subtítulo del libro describen el arco completo.
- El orden de las páginas es importante: page_001 al page_N sigue la secuencia del arco.
"""


def _lookup_story(pack, story_id: str) -> dict | None:
    """Read a story definition from pack.stories.stories (defined in stories.yaml)."""
    stories = (getattr(pack, "stories", None) or {})
    if not isinstance(stories, dict):
        return None
    all_stories = stories.get("stories", [])
    if not isinstance(all_stories, list):
        return None
    for s in all_stories:
        if isinstance(s, dict) and s.get("id") == story_id:
            return s
    return None


def _lookup_characters(pack) -> dict[str, dict]:
    """Read the pack-level character roster (stories.yaml top-level `characters`)."""
    stories = (getattr(pack, "stories", None) or {})
    if not isinstance(stories, dict):
        return {}
    roster = stories.get("characters", [])
    if not isinstance(roster, list):
        return {}
    return {c.get("id"): c for c in roster if isinstance(c, dict) and c.get("id")}


def _characters_block(pack, story: dict | None) -> str:
    """Build the character-consistency block for the concept prompt.

    Includes the full roster (so the LLM knows the universe) and marks which
    characters are present in this specific story. Names are identical in all
    languages (franchise-style).
    """
    roster = _lookup_characters(pack)
    if not roster:
        return ""
    present_ids = set(story.get("characters_present", [])) if story else set()
    lines = ["PERSONAJES DEL UNIVERSO (nombres idénticos en todos los idiomas):"]
    for cid, c in roster.items():
        role = c.get("role", "supporting")
        present = "PRESENTE EN ESTE LIBRO" if cid in present_ids else "no aparece en este libro"
        desc = c.get("description_en") or c.get("archetype_en") or cid
        lines.append(f"- {c.get('name_en', cid)} ({role}, {present}): {desc}")
    lines.append(
        "REGLAS: el personaje main debe aparecer en TODAS las páginas con diseño idéntico. "
        "Los personajes supporting presentes pueden aparecer en 4-6 páginas cada uno, "
        "siempre con su diseño fijo. Nunca inventes personajes fuera de esta lista."
    )
    lines.append(
        "REPARTO POR PÁGINA: cada página debe declarar en el campo `characters` los IDs "
        "de los personajes que aparecen en ella (el main SIEMPRE incluido). "
        "Solo IDs de la lista anterior. Si un personaje supporting no aparece en un beat, no lo incluyas."
    )
    return "\n".join(lines)


def _build_prompt(pack, request: ProductRequest) -> str:
    """Build the concept prompt from the full Pack (not just the profile).

    The pack carries stories.yaml (roster, story arcs) which the concept
    prompt needs for story mode.
    """
    profile = pack.profile
    lang_str = ", ".join(profile.languages)
    story = _lookup_story(pack, request.story_id) if request.story_id else None

    if story:
        # Story mode: enforce the arc from stories.yaml
        beats = story.get("arc", story.get("beats", story.get("beat", [])))
        title_en = story.get("title_en", story.get("title", request.theme))
        title_es = story.get("title_es", title_en)
        char_desc = request.character or "the protagonist"
        beats_text = "\n".join(f"  - {b}" for b in beats)
        characters_block = _characters_block(pack, story)
        return f"""HISTORIA PREDEFINIDA (debes respetarla fielmente):

Pack: {profile.id}
Título del libro (EN): {title_en}
Título del libro (ES): {title_es}
Tema creativo (distinto del ID de historia): {request.theme}
Personaje principal (DESIGN IDÉNTICO en TODAS las páginas): {char_desc}
Número de páginas: {request.page_count}
Idioma: {lang_str}

{characters_block}

Arco narrativo (cada página = un beat):
{beats_text}

{_STORY_RULES}

{_COMPOSITION_RULES_V2}

Devuelve SOLO JSON:
{{
  "pages": [
    {{
      "id": "page_001",
      "index": 1,
      "beat": "<nombre del beat correspondiente>",
      "characters": ["<id del personaje main>", "<id de secundario si aparece>"],
      "prompt": "<prompt detallado en inglés para image gen — describe personaje consistente + beat + escenografía>",
      "title": "<título de la escena en inglés>"
    }}
  ],
  "titles": {{"en": "{title_en}", "es": "{title_es}"}},
  "description_hint": "<1 frase descriptiva>"
}}

IMPORTANTE: cada beat debe corresponder a su posición en el arco. El MISMO personaje debe aparecer en cada página con la MISMA descripción física (color, tamaño, forma, cara).
"""
    else:
        # Theme mode (freeform): no predefined arc
        return f"""Genera un plan de un producto digital.

Pack: {profile.id}
Tipo: {profile.pack_type}
Tema: {request.theme}
Personaje: {request.character}
Número de páginas: {request.page_count}
Idiomas: {lang_str}
Pista de título: {request.title_hint or "(ninguna)"}

{_COMPOSITION_RULES_V2}

Devuelve SOLO JSON con esta estructura:
{{
  "pages": [
    {{"id": "page_001", "index": 1, "beat": "<beat if applicable>", "prompt": "<prompt detallado en inglés para generar imagen>", "title": "<título corto>"}}
  ],
  "titles": {{"en": "<título en inglés>", "es": "<título en español>"}},
  "description_hint": "<1 frase descriptiva>"
}}

Cada prompt debe diseñar UNA PÁGINA completa, con el sujeto centrado y rodeado de aire. Los prompts deben ser variados dentro del tema ({request.theme}).
"""


def _slug(pack_id: str, theme: str) -> str:
    import re

    base = f"{pack_id}-{theme}"
    return re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-")


class ConceptStage(Stage):
    stage_name = "concept"
    inputs: ClassVar = []
    outputs: ClassVar = ["concept"]
    prompt_version = PROMPT_VERSION

    def run(self, ctx: StageContext, **inputs: BaseModel) -> ProductPlan:
        prompt = _build_prompt(ctx.pack, ctx.request)
        result = retry_parse(
            ctx.llm,
            _SYSTEM,
            prompt,
            ConceptSchema,
            on_response=lambda r: ctx.set_cost(estimate_cost(r)),
        )

        pages: list[PageSpec] = []
        for i, p in enumerate(result.pages):
            raw_characters = [c for c in (p.get("characters") or []) if isinstance(c, str)]
            try:
                characters = normalize_character_ids(ctx.pack, raw_characters)
            except ValueError as exc:
                raise RuntimeError(f"invalid character roster in page {i + 1}: {exc}") from exc
            pages.append(
                PageSpec(
                    id=p.get("id", f"page_{i + 1:03d}"),
                    index=p.get("index", i + 1),
                    prompt=p.get("prompt", ""),
                    title=p.get("title", p.get("id", f"page_{i + 1:03d}")),
                    theme=ctx.request.theme,
                    beat=p.get("beat", ""),
                    characters=characters,
                )
            )

        # Validate page IDs: unique and non-empty
        seen_ids: set[str] = set()
        for p in pages:
            if not p.id:
                raise RuntimeError(f"concept produced a page with empty id at index {p.index}")
            if p.id in seen_ids:
                raise RuntimeError(f"concept produced duplicate page id: {p.id}")
            seen_ids.add(p.id)

        # Validate page count matches request
        if len(pages) != ctx.request.page_count:
            raise RuntimeError(
                f"concept produced {len(pages)} pages, expected {ctx.request.page_count}"
            )

        return ProductPlan(
            pack_id=ctx.pack.profile.id,
            pack_version=ctx.pack.profile.pack_version,
            theme=ctx.request.theme,
            pages=pages,
            titles=result.titles,
            description_hint=result.description_hint,
        )
