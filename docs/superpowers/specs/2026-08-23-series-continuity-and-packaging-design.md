# Series Continuity And Packaging Design

**Status:** superseded by the franchise catalog layout (see `cocholate/`, `src/productfoundry/catalog.py` and the README). Kept for history only; the current design owns characters at the franchise level, packs as reusable recipes, and books with per-pack editions.

## Goal

Keep recurring characters visually stable across books in the same series while allowing explicitly registered new characters, and correct the real package's cover composition and localized copy.

## Decisions

- A pack may declare a `series.yaml` registry with a series ID, registry version, and character contracts.
- Each registered character has a stable ID, a definition hash, and a versioned canonical reference image inside the pack.
- Existing character definitions are immutable while the registry version remains unchanged. A changed definition requires a deliberate registry version increment.
- A new character is allowed only when it is added to the registry with its own stable ID, definition hash, and canonical reference image.
- Character sheets are copied from the pack registry into each project's assets directory; existing characters are not regenerated per book.
- Generation may vary pose, expression, and scene, but must use the canonical reference for fixed identity traits.
- Back-cover thumbnails preserve the complete page inside a white frame instead of center-cropping it.
- Spine text is omitted. Front-cover title and subtitle remain embedded in the hero artwork.
- Localized story subtitles are used for cover copy; the LLM's free-form fallback is used only when the story has no localized subtitle.
- Age badges use `Edad <range>` for Spanish and `Ages <range>` for English.

## Acceptance Criteria

- Two products from one series use byte-identical canonical reference images for every shared character.
- Changing an existing character's locked definition without incrementing the series registry fails validation before image generation.
- Adding a new registered character does not alter any existing character reference.
- A missing canonical reference fails closed with an actionable error.
- A back-cover thumbnail has the full source aspect ratio visible and white padding where needed.
- Generated wrap covers contain no title or author text on the spine.
- Spanish covers use `subtitle_es`; English covers use `subtitle_en`.
- Spanish covers use `Edad 3-8`; English covers use `Ages 3-8`.
- Existing tests and new continuity/packaging tests pass.
