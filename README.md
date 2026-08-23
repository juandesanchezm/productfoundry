# productfoundry

Product-agnostic digital product engine. Given a franchise catalog (characters, packs, series and books), generates AI assets, post-processes, packages into digital/print formats in multiple languages, produces SEO listings, and prepares the manual KDP upload kit.

## Catalog layout

A **franchise** is a project directory under `projects/` that owns its intellectual property. Everything else hangs from it:

```
projects/
  cocholate/
    characters/              # canonical characters (definition + reference PNG)
      cocholate.yaml  cocholate.png
      pip.yaml        pip.png
      pebble.yaml     pebble.png
      clover.yaml     clover.png
    packs/                   # reusable production recipes
      coloring-fantasy/
        pack.yaml  style.yaml  packaging.yaml  quality.yaml  audit.yaml  listing.yaml  compliance.yaml
    series/                  # series of books
      cocholate-adventures/
        series.yaml
        books/
          magical-day/
            book.yaml
            editions/        # (gitignored) one concrete execution per pack
              coloring-fantasy/
                product.json
                assets/      # raw generated images
                processed/   # cleaned B/W art
                packages/    # digital PDFs, print PDFs, covers, kdp_upload kit
                listings/    # SEO metadata per marketplace × language
                artifacts/   # per-gate records (concept, printcheck, release...)
          tea-party/
          forest-explorer/
```

Ownership rules:

- `characters/` — canonical characters, shared by every series and book.
- `packs/` — recipes (trim size, style, compliance) reusable across books.
- `series/` — series of books; each book's `book.yaml` carries titles, blurb and the page arc.
- `editions/<pack>/` — the concrete run of a book with a pack; all generated assets live here (gitignored).

The engine stays niche-agnostic: `src/` contains no character or niche knowledge (enforced by `tests/test_agnosticism.py`).

## Print model (KDP paperback)

- **Interior:** trim-size PDF (8.5x11), no bleed, 300 DPI, with a white ink-safe margin of 0.375in (KDP minimum for 24-150 page books). Line-art pages never bleed.
- **Cover:** wrap PDF (back + spine + front) with the standard 0.125in bleed on all sides; KDP places the ISBN barcode automatically.
- **Manual upload:** `packages/kdp_upload/<lang>/` contains the two files KDP needs (interior PDF + cover PDF) plus a `kdp-checklist.md` with the exact upload steps.

## Quick start

```bash
uv sync
set -a; source .env; set +a   # loads OPENAI_API_KEY, OLLAMA_API_KEY

# Validate the franchise catalog (characters, packs, series, books, contracts)
uv run productfoundry validate cocholate

# Generate a book end-to-end (placeholders for offline smoke: runtime/smoke.yaml)
uv run productfoundry create \
  --franchise cocholate --series cocholate-adventures --book magical-day \
  --pack coloring-fantasy --theme "magical-day" \
  --runtime runtime/smoke.yaml

# Real generation
uv run productfoundry create \
  --franchise cocholate --series cocholate-adventures --book magical-day \
  --pack coloring-fantasy --theme "magical-day"

uv run productfoundry resume magical-day-coloring-fantasy --franchise cocholate
uv run productfoundry status magical-day-coloring-fantasy --franchise cocholate
uv run productfoundry release magical-day-coloring-fantasy --franchise cocholate --approve
uv run productfoundry list
```

## Environment

- `OPENAI_API_KEY` — image generation (gpt-image).
- `OLLAMA_API_KEY` — Ollama-compatible LLM (concept + listings).
- `PRODUCTFOUNDRY_FRANCHISES_DIR` — override the franchise root (default: `projects/`).
- `PRODUCTFOUNDRY_PROJECTS_DIR` — legacy (non-franchise) product outputs (default: `projects/legacy/`).

Both keys are loaded from `.env` (gitignored) via `set -a; source .env; set +a`.

## Principles

1. **Engine is content-agnostic.** No niche knowledge in `src/` (see `tests/test_agnosticism.py`).
2. **Three independent configs.** `RuntimeProfile`, `PackProfile`, `ProductRequest`.
3. **Franchise owns the IP.** Characters live once under `characters/` and are referenced, never copied, by books.
4. **Pydantic contracts between stages.**
5. **Deterministic packaging.** The LLM expresses intent; the code (img2pdf, Pillow) builds PDFs.
6. **Content-hash caching.** Re-run only what changed.
7. **Human approval required.** A product is publishable only after `productfoundry release <id> --approve`.
