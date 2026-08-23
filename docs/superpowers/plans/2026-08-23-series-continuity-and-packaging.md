# Series Continuity And Packaging Implementation Plan

> **Status:** superseded by the franchise catalog layout (`cocholate/`, `src/productfoundry/catalog.py`). Kept for history only.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist canonical series characters across volumes and fix the real cover's thumbnail, spine, and subtitle behavior.

**Architecture:** Load an optional versioned `series.yaml` and canonical PNG registry into the existing `Pack`. Validate story roster definitions against the registry before generation, copy canonical references into project assets, and keep the existing image-edit routing unchanged. Make cover composition deterministic with a contain helper and localized story copy.

**Tech Stack:** Python 3.12, Pydantic, PyYAML, Pillow, pytest, existing ProductFoundry pipeline.

**Spec:** `docs/superpowers/specs/2026-08-23-series-continuity-and-packaging-design.md`

## Global Constraints

- The engine remains niche-agnostic; series data lives in pack files.
- Existing character IDs and definitions are immutable unless the series registry version is incremented.
- New characters require stable IDs and canonical reference images.
- The judge remains enabled for the real package.
- No synthetic barcode or publish approval is added.

---

### Task 1: Load And Validate Series Contracts

**Files:**
- Create: `packs/coloring-fantasy/series.yaml`
- Create: `src/productfoundry/series.py`
- Modify: `src/productfoundry/pack_loader/__init__.py`
- Modify: `src/productfoundry/domain/pack.py`
- Modify: `src/productfoundry/engine/pipeline.py`
- Modify: `src/productfoundry/stages/pack_validate.py`
- Test: `tests/test_series.py`

**Interfaces:**
- `Pack.series` stores parsed series data.
- `Pack.root` stores the pack directory for resolving canonical references.
- `validate_series_contract(pack) -> list[str]` returns all immutable-roster and reference errors.
- `canonical_character_reference(pack, character_id) -> Path` returns a required canonical PNG path.

- [ ] Write tests for valid contracts, changed locked definitions, missing references, and registered new characters.
- [ ] Run the focused tests and confirm they fail before implementation.
- [ ] Add `series.yaml` with the current Blaze, Pip, Pebble, and Clover IDs and their definition hashes.
- [ ] Load `series.yaml` and pack root without breaking packs that do not declare a series.
- [ ] Implement stable JSON hashing of the identity fields from `stories.yaml`.
- [ ] Make `PackValidationStage` fail closed on contract errors.
- [ ] Include series data and canonical reference hashes in pipeline cache inputs.
- [ ] Run focused tests and the full existing suite.

### Task 2: Use Canonical References In Generation

**Files:**
- Add: `packs/coloring-fantasy/characters/blaze.png`
- Add: `packs/coloring-fantasy/characters/pip.png`
- Add: `packs/coloring-fantasy/characters/pebble.png`
- Add: `packs/coloring-fantasy/characters/clover.png`
- Modify: `src/productfoundry/stages/character_sheet.py`
- Modify: `src/productfoundry/stages/assets.py`
- Modify: `src/productfoundry/stages/hero.py`
- Test: `tests/test_series.py`

**Interfaces:**
- `CharacterSheetStage` copies canonical registry images into `project/assets/character_sheet_<id>.png` without calling the image provider.
- Existing page and hero reference routing continues to consume project-local copies.

- [ ] Copy the approved current character sheets into the versioned pack registry.
- [ ] Add a failing test proving the image provider is not called for existing canonical characters.
- [ ] Implement canonical copy and legacy main-sheet synchronization.
- [ ] Add canonical reference hashes to `CharacterSheetStage.extra_hash_inputs`.
- [ ] Preserve the existing per-page reference routing for only declared characters.
- [ ] Run focused tests and verify zero image-provider calls for registered characters.

### Task 3: Fix Cover Composition And Localization

**Files:**
- Modify: `src/productfoundry/packaging/__init__.py`
- Modify: `src/productfoundry/stages/package.py`
- Modify: `src/productfoundry/stages/hero.py`
- Create: `src/productfoundry/stages/story_helpers.py`
- Test: `tests/test_packaging.py`

**Interfaces:**
- `ImageOps_contain(image, width, height, background=(255,255,255)) -> Image.Image` preserves the complete source image.
- `localized_story_subtitle(pack, story_id, language, fallback) -> str` returns the requested story subtitle.

- [ ] Write tests for thumbnail aspect preservation, absent spine text, Spanish/English subtitle lookup, and localized age labels.
- [ ] Run focused tests and confirm failure.
- [ ] Use contain only for back-cover thumbnails; keep fit for full-page and front-cover artwork.
- [ ] Remove the spine title/author drawing block.
- [ ] Use localized story subtitles in both hero embedding and wrap-cover construction.
- [ ] Pass the requested language to wrap-cover construction so age badges are localized.
- [ ] Run focused tests and the full suite.

### Task 4: Rebuild And Verify The Real Product

**Files:**
- Modify: `projects/real-coloring-es/` generated outputs only

- [ ] Resume `real-coloring-es` with the judge enabled and preserve canonical references.
- [ ] Inspect the regenerated wrap cover and representative back-cover thumbnails.
- [ ] Verify 24-page digital/print outputs, ZIP integrity, localized listings, and all quality gates.
- [ ] Run `uv run pytest -q`, targeted Ruff, and `compileall`.
- [ ] Report the remaining human approval requirement without marking the product publishable.
