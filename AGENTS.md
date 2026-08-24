# AGENTS.md

Guía para agentes que trabajan en este repositorio.

## Qué es este proyecto

ProductFoundry: pipeline de generación de libros para colorear kawaii (kids activity books) con imágenes IA, juez vision, packaging KDP/Etsy/Gumroad y listing SEO. Catálogo por franquicias:

- `projects/<franchise>/characters/` — personajes canónicos (YAML de identidad + PNG line-art de referencia).
- `projects/<franchise>/series/<series>/series.yaml` — contrato inmutable del roster: `reference_image` + `definition_hash` por personaje + `version`.
- `projects/<franchise>/series/<series>/books/<book>/book.yaml` — historia: `title_en/es`, blurb, `arc` (24 beats), `characters_present`.
- `projects/<franchise>/packs/<pack>/` — `pack.yaml` (perfil), `style.yaml`, `packaging.yaml`, `compliance.yaml`, `listing.yaml`, `quality.yaml`, `audit.yaml`.
- Ediciones generadas: `projects/<franchise>/series/<series>/books/<book>/editions/<pack>/` (ignoradas en git; los catálogos sí se versionan).

## Comandos

```bash
uv run productfoundry validate cocholate       # valida catálogo (roster, hashes, referencias)
uv run productfoundry create --pack coloring-fantasy --theme sunny-meadow --story magical-day --product-id X --franchise cocholate --series cocholate-adventures --book magical-day --runtime runtime/real.yaml [--force] [--no-audit]
uv run productfoundry resume <product-id> --franchise cocholate [--start-at <stage>]
uv run productfoundry status <product-id> --franchise cocholate
uv run productfoundry list
uv run productfoundry release <product-id> --franchise cocholate --approve   # aprobación humana (crea .release_approved con fingerprint)
uv run pytest -q -p no:cacheprovider           # suite completa (83 passed, 1 skipped esperado)
uv run ruff check .
uv run python scripts/smoke_test.py            # smoke determinista con placeholders (sin coste, ejecución parcial sin release)
```

Runtimes: `runtime/default.yaml` (real, budget 20.0), `runtime/real.yaml` (real, budget 5.0, cover attempts [high]), `runtime/smoke.yaml` (placeholders, budget 0.01).

## Reglas críticas

- **Hashes**: cada cambio en `description_en/es` o `archetype` de un personaje exige recalcular `definition_hash` con `character_definition_hash()` (desde `src/productfoundry/series.py`) y **bumpear `series.version`**.
- **Referencias canónicas**: las imágenes del roster viven en `characters/`; los stages las leen directamente (`canonical_character_reference`). Nunca regenerar un PNG canónico dentro de una edición.
- **Fail-closed**: un `judge parse failure` es FAIL, nunca ok. No convertir fallos del juez en aprobaciones.
- **Gates**: `audit_prompt`, `pack_validate`, `audit_character_sheet`, `audit_assets`, `lineart_check`, `printcheck`, `review` deben devolver `pass`; `release` exige aprobación humana con fingerprint de los deliverables.
- **Portada**: el texto va embebido por el modelo en la imagen (zona de texto separada). El juez vision (`minimax-m3`, configurable vía `audit.yaml`) verifica el copy letra a letra y puede regenerar hasta 3 intentos.
- **Idiomas**: el pack `coloring-fantasy` genera SOLO `en` (decisión de coste; los helpers de localización soportan más idiomas).
- **Agnosticismo**: ningún prompt hardcodea "children", "coloring", "amazon", etc. El pack declara su nicho vía `PackProfile` (audience, age_range, theme).
- **Sin subtítulos**: se eliminó `subtitle_*` del pipeline (portada = título + serie + edad + autor).
- **Entorno**: el `.env` se carga automáticamente desde la raíz del repo o cwd (`cli.py`); no hace falta exportar manualmente `OPENAI_API_KEY`/`OLLAMA_API_KEY`.

## Pipeline (orden)

`concept → audit_prompt → pack_validate → character_sheet → audit_character_sheet → assets → audit_assets → postprocess → lineart_check → hero → back_cover → package → printcheck → listing → review → release`

- `concept`: expande la historia en 24 páginas (arc del book.yaml).
- `character_sheet`: rostros canónicos (no duplica si hay PNG canónico).
- `assets`: genera las 24 páginas con referencia por personaje.
- `hero`: una portada por idioma (`cover_hero_<lang>.png`).
- `package`: PDF interiores + wrap cover (KDP) + ZIP digitales + `kdp_upload/`.
- `listing`: matriz marketplace × formato × idioma (`<marketplace>-<format>-<lang>.json`).

## Decisiones de diseño recientes

- Reanudar el pipeline con `resume --start-at` persiste la invalidación.
- `.pipeline.lock` evita doble ejecución; escrituras atómicas en `product.json`.
- `publication-manifest.json` lista deliverables con SHA-256, gates y fingerprint; `publishable` solo con aprobación humana y no-sintético.
- Cached: hash de código + prompt_version + config + inputs + providers; `.design_hash` invalida páginas cuando cambia el personaje.
- `--theme` (tema creativo del prompt) es distinto de `--story` (id en `book.yaml` que activa modo historia); no deben repetir el mismo nombre.
- `--no-audit` evita el juez vision (más rápido y barato) pero desactiva los gates de auditoría.
