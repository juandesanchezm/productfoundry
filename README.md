# productfoundry

Product-agnostic digital product engine. Given a pack (niche), generates AI assets, post-processes, packages into digital/print formats in multiple languages, and produces SEO listings ready for Etsy, Gumroad, and Amazon KDP.

## Architecture

```
ProductRequest (pack, theme, pages, languages, formats)
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│  Engine (pipeline executor: state + cache + node graph)      │
│                                                              │
│  RuntimeProfile ──► PackProfile ──► 6 stages:                │
│  (providers,      (niche pack:   concept → assets →          │
│   model ids,       languages,     postprocess → package →    │
│   budgets)         formats)       listing → review          │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
projects/<id>/
  assets/      # raw generated images
  processed/   # cleaned B/W art
  packages/    # digital PDFs, print-ready PDFs (KDP), ZIPs, covers
  listings/    # SEO metadata per marketplace × language
  product.json
```

- **Engine** — DAG pipeline with state persistence and content-hash caching; invalidated nodes re-execute on `resume`.
- **RuntimeProfile** — global runtime config: provider credentials, model ids, per-stage budgets.
- **PackProfile** — per-niche pack: languages, formats, marketplaces, page size, themes.
- **ProductRequest** — the specific product (pack + theme + pages + languages + formats).

## Quick start

```bash
uv sync
set -a; source .env; set +a   # loads OPENAI_API_KEY, OLLAMA_API_KEY

# Packs
uv run productfoundry pack create mypack       # copy the example template
uv run productfoundry pack validate mypack

# Products
uv run productfoundry create --pack example --theme "dragons" --pages 5 --languages en,es --formats digital,print
uv run productfoundry resume <product_id>
uv run productfoundry status <product_id>
uv run productfoundry list
```

## Environment

- `OPENAI_API_KEY` — image generation (gpt-image).
- `OLLAMA_API_KEY` — Ollama-compatible LLM (concept + listings).

Both are loaded from `.env` (gitignored) via `set -a; source .env; set +a`.

## Principles

1. **Engine is content-agnostic.** No niche knowledge in `src/` (see `tests/test_agnosticism.py`).
2. **Three independent configs.** `RuntimeProfile`, `PackProfile`, `ProductRequest`.
3. **Pydantic contracts between stages.**
4. **Deterministic packaging.** The LLM expresses intent; the code (img2pdf, Pillow) builds PDFs.
5. **Content-hash caching.** Re-run only what changed.
