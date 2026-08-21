# productfoundry — Plan de implementación

Fecha: 2026-08-21
Estado: en progreso

## M1 — Esqueleto, engine, providers, CLI ✅

- Repo + pyproject.toml + uv sync
- Engine: pipeline.py, state.py, cache.py, hashing.py, provenance.py
- Domain models: pack, product, assets, packaging, listing, review
- Providers: image (OpenAI + placeholder), llm (Ollama + placeholder + openai)
- Runtime: default.yaml + smoke.yaml
- CLI: pack create/validate, create, resume, status, list
- Test agnosticismo: tests/test_agnosticism.py
- Verificado: `uv run pytest` (2 passed), `productfoundry create --runtime runtime/smoke.yaml` end-to-end

## M2 — Stages y pack de coloreado ✅

- Stages: concept, assets, postprocess, package, listing, review
- Packaging determinista: img2pdf (interior), Pillow (cover), ZIP
- Pack inicial: coloring-fantasy (8 sub-themes: dragons, dungeons, taverns, wizards, monsters, undead, forest, sea)
- Verificado: smoke test genera 3 páginas → 8 paquetes (PDF + ZIP × 2 idiomas × 2 marketplaces digitales)

## M3 — Print KDP, listings EN/ES, review ✅

- Print packaging con bleed (0.125") + cover separado
- Listings generados en EN + ES por marketplace
- Review gate determinista (existencia de assets, paquetes, listings)
- Cross-language: confirma 2 idiomas × 2 marketplaces digitales en smoke

## M4 — Primer lote real y subida manual

- Reemplazar runtime con API keys reales (OLLAMA_API_KEY + OPENAI_API_KEY)
- Generar 60-100 páginas reales (tema "dragons" del pack coloring-fantasy)
- Subir manualmente: Etsy (PDF + ZIP digital), Gumroad, KDP (interior PDF + cover PNG)
- Coste estimado: ~5-10€ en API

## M5 — Pack battlemaps

- Nuevo pack: battlemaps (TTRPG)
- Postprocess con grid overlay (determinista)
- Formatos VTT (Roll20, Foundry)
- Iteración: battlemaps vs fantasy comparten motor

## M6 — Publicación automatizada

- API de Gumroad (token simple)
- API de Etsy (OAuth2 — más setup)
- Mantener KDP manual (sin API pública)
