"""audit stage — LLM-as-judge gate for prompts (text) and assets (vision).

Runs twice in the DAG:
  - after `concept`: audits each prompt for composition that tends to produce
    anatomical artifacts. Recalibrated (v2) to allow scenery, props, and one
    mini companion; flags fails only on 2nd large figure, hands holding
    complex objects, cropping, and missing protagonist.
  - after `assets`: audits each generated image. Anatomical checks are
    blocking; aesthetic checks (cuteness, kid-appropriateness) are
    informational notes and do not flip the verdict.

Skip this stage (in dry-runs / fast iteration) by setting
`audit.enabled: false` in the pack or by passing `--no-audit` to the CLI.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, ClassVar

from productfoundry.domain.assets import AssetPlan
from productfoundry.domain.audit import (
    AssetAuditReport,
    AuditVerdict,
    PromptAuditReport,
)
from productfoundry.domain.product import ProductPlan
from productfoundry.engine.pipeline import Stage, StageContext
from productfoundry.providers.llm import _strip_json_fence
from productfoundry.stages.helpers import estimate_cost

PROMPT_VERSION = "audit-v2"
JUDGE_MODEL = "minimax-m3"  # vision-capable, available on Ollama Cloud

PROMPT_SYSTEM = (
    "You are a strict quality auditor for printable line-art images. "
    "You always reply with valid JSON only, no prose, no markdown fences."
)

PROMPT_USER_TEMPLATE = """You are the storytelling judge for an image generation pipeline that produces
black-and-white line art for a children's activity book.

The composition rules accepted by this pipeline:
- 1 protagonist (centered, full body visible, consistent across pages)
- PLUS: scenery (castles, forests, rooms, etc.) is ALLOWED
- PLUS: inanimate props (chests, teapots, books, lanterns) NOT in the
  protagonist's hands are ALLOWED
- PLUS: up to 2 mini companions (each <20% of protagonist size: small snail,
  bird, fireflies, kitten) are ALLOWED when the scene remains readable

Reject (mark "fail") only when:
- 2nd large figure (size comparable to protagonist) appears in the scene
- Protagonist has hands holding complex objects (swords, shields, tools)
- Subject is cropped at the edges or not centered
- Multiple faces or close-up faces
- Subject is described ambiguously (e.g., "a creature" without specifying)

Also evaluate the book as a sequence, not just isolated pages:
- The prompt must honor its story beat and expected characters.
- The sequence must have clear variety in action, setting, camera distance, or composition;
  reject repetitive pages that would make the activity book monotonous.
- The scene should be playful, surprising, and age-appropriate for children 3-8.
- Include clear, large, colorable shapes and props that invite the child to participate.
- Reject a page that is technically clean but emotionally flat, confusing, or unrelated
  to its beat. Use "warn" when it is usable but needs a prompt rewrite.

For every page, verify the story beat, expected characters, detailed scene,
action, setting, and sequence continuity. Return exactly one verdict per page
in the same order as the supplied pages; missing or extra verdicts are invalid.

For each prompt, return a JSON object with:
- "status": "ok" | "warn" | "fail"
- "notes": one short sentence with the issue, or "" if ok
- "rewrite_suggestion": ONLY if status is "warn" or "fail", a single-sentence
  rewrite of the prompt that fixes the issue. Otherwise "".

Pages and prompts:
{prompts_json}

Return JSON only:
{{"verdicts": [
  {{"status": "...", "notes": "...", "rewrite_suggestion": "..."}},
  ...
]}}
"""

IMAGE_USER_TEMPLATE = """Audit each generated image for a printable children's art book.

These images are BLACK-AND-WHITE LINE ART. Color is NOT present in line art,
so never evaluate color — evaluate SHAPE and structure only: head shape and
size, body proportions, wings/tail/ears, expression, pose.

Main character characterization (the character in the image MUST match this shape):
{characterization}

Audience/niche: {audience}. Reject (fail) any image that is not appropriate
for this audience (scary, violent, adult themes, overly complex for the age).

Story context for this page:
- Expected beat: {beat}
- Expected characters: {characters}
- Planned scene: {page_prompt}

The image must be playful and engaging for a child to color. Look for a clear
action, a readable scene, varied composition, and inviting large shapes or props.
Reject a technically clean image when it is bland, repetitive, disconnected from
  the beat, or offers no recognizable activity opportunity.

For each image, return a JSON object with:
- "status": "ok" | "warn" | "fail"
- "notes": one short sentence with the issue, or "" if ok
- "cuteness": one short sentence describing how cute/kid-appropriate the image is (e.g. "adorable proportions, friendly expression" or "too realistic, scary, not for kids")
- "rewrite_suggestion": ONLY if status is "warn" or "fail", a single-sentence
  rewrite of the prompt that fixes the issue. Otherwise "".

Status rules:
- "ok": clean, ready to print
- "warn": any minor doubt (cropping near edge, slight asymmetry, line thickness issue). Lean towards warn if unsure.
- "fail": clear anatomical artifact, severe crop, OR the main character does not
  match its characterization shape (wrong species, missing key traits such as
  wings or tail, wrong proportions), OR the image is not appropriate for the audience.

Detect these issues (which are common in diffusion models):
- Extra or missing limbs (extra arms, hands, legs, fingers)
- Hands in anatomically wrong places or holding objects across the face
- Protagonist (the main character) cropped at the frame edges
- Two subjects of similar size blurred together (small companion is OK)
- Image mostly blank (only background, no subject)
- Major anatomical artifacts (faces overlapping, twisted pose)
- Character mismatch: the main character differs from its characterization shape

Image #{index}:
"""

SHEET_USER_TEMPLATE = """Audit this character reference sheet against its official characterization.

Characterization (the character MUST match this exactly):
{characterization}

Evaluate only the listed traits; do not invent additional requirements that are
not present in the characterization.
This is black-and-white line art: do not fail because color is not visible.

Check that the image matches the characterization:
- Species/type matches (e.g. a baby animal, not a different creature)
- Key physical traits present (colors, wings, tail, ears, size proportions)
- Style matches (cute proportions, friendly expression)
- Full body visible, front view, neutral pose
- No text, no watermark, no extra characters

Return JSON only:
{{"status": "ok|warn|fail", "notes": "one short sentence", "rewrite_suggestion": "single-sentence fix if not ok, else empty"}}
"""

BACK_USER_TEMPLATE = """Audit this BACK COVER background illustration for a children's book.

This is a FULL-COLOR illustration (NOT line art). It will receive four
interior-page thumbnails in the upper area and KDP's ISBN barcode in the
lower area — so it must leave room for them.

Audience/niche: {audience}. Reject (fail) any image that is not appropriate
for this audience (scary, violent, inappropriate themes).

Check:
- The scene is a soft, dreamy background (forest, meadow, sky)
- NO characters, NO animals, NO figures of any kind: pure scenery only
- The upper area is calm and clean (thumbnail zone), the lower area is quiet
  and mostly empty (barcode zone)
- NO text, NO letters, NO words, NO sign, NO banner, NO watermark, NO signature anywhere
- No anatomy artifacts, no multiple large figures
- The image is vibrant but gentle, not busy, not cluttered

Reject (fail) when: any character or animal appears, any text appears, the
center is cluttered, the bottom is busy, or the image is dark/scary.

Return JSON only:
{{"status": "ok|warn|fail", "notes": "one short sentence", "rewrite_suggestion": "single-sentence fix if not ok, else empty"}}
"""

HERO_USER_TEMPLATE = """Audit this cover illustration for a children's book.

This is a FULL-COLOR cover illustration (NOT line art). Evaluate color, composition,
character identity, and text.

Main character characterization (the character in the image MUST match this shape):
{characterization}

Audience/niche: {audience}. Reject (fail) any image that is not appropriate
for this audience (scary, violent, adult themes).

The cover REQUIRES the exact title text embedded by the artist in a clearly
separated text zone (a sign, banner, cloud, arch or frame). The expected copy
is given below; verify it letter by letter, including accents and punctuation.

Expected title: {expected_title}
Expected subtitle: {expected_subtitle}
Expected age badge: {expected_age_badge}
Expected author name: {expected_author}

Check:
- The protagonist is centered and clearly the hero of the cover
- The protagonist matches its characterization (correct species, key traits,
  proportions, and official colors when specified)
- The composition has a clearly separated text zone (sign, banner, cloud, arch or frame)
- The text inside that zone is a single language, fully readable, WITHOUT spelling
  errors, and matches the expected copy EXACTLY (title, subtitle, age badge, author)
- NO extra words, letters, watermark, or signature anywhere else in the image
- The image is vibrant and appealing (not dull, dark, or scary)
- No anatomical artifacts (extra limbs, distorted features)

Reject (fail) when: any text is misspelled or missing, accents are wrong or missing,
the text is a different language than expected, the text zone is missing, or any
unexpected text appears anywhere in the image. The copy is the product contract:
it must be exactly as provided.

Return JSON only:
{{"status": "ok|warn|fail", "notes": "one short sentence", "rewrite_suggestion": "single-sentence fix if not ok, else empty"}}
"""


def _is_audit_enabled(pack) -> bool:
    """Pack can opt-out of the audit gate via `audit.enabled: false`,
    or globally via env var `PRODUCTFOUNDRY_SKIP_AUDIT=1`."""
    import os

    if os.getenv("PRODUCTFOUNDRY_SKIP_AUDIT") == "1":
        return False
    audit_cfg = getattr(pack, "audit", None) or {}
    if isinstance(audit_cfg, dict):
        nested = audit_cfg.get("audit", audit_cfg)
        if isinstance(nested, dict):
            return bool(nested.get("enabled", True))
    return True


def _judge_model(pack) -> str:
    """Judge model from the pack's audit.yaml (`audit.judge_model`), falling
    back to the engine default. The pack decides which vision model to use."""
    audit_cfg = getattr(pack, "audit", None) or {}
    if isinstance(audit_cfg, dict):
        nested = audit_cfg.get("audit", audit_cfg)
        if isinstance(nested, dict) and nested.get("judge_model"):
            return str(nested["judge_model"])
    return JUDGE_MODEL


def _coerce_status(raw: str) -> str:
    """Fail-closed coercion: only explicit 'ok'/'warn'/'fail' pass through;
    anything else (invalid JSON, unknown status, empty) becomes 'fail'."""
    if raw in ("ok", "warn", "fail"):
        return raw
    return "fail"


def _parse_judge_json(content: str) -> dict:
    """Parse a judge response while tolerating harmless wrapper text."""
    cleaned = _strip_json_fence(content)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def _complete_with_image_json(
    ctx: StageContext,
    system: str,
    user: str,
    image_b64: str,
    model: str,
    max_attempts: int = 2,
) -> dict | None:
    """Retry malformed vision-judge output without regenerating the image."""
    for _ in range(max_attempts):
        response = ctx.llm.complete_with_image(system, user, image_b64, model=model)
        ctx.set_cost(estimate_cost(response))
        try:
            data = _parse_judge_json(response.content)
            if not isinstance(data, dict):
                raise TypeError("judge response must be a JSON object")
            return data
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return None


def _audit_prompts(ctx: StageContext, plan: ProductPlan) -> PromptAuditReport:
    try:
        from productfoundry.stages.assets import build_page_prompt

        prompt_rows = [
            {
                "id": p.id,
                "index": p.index,
                "beat": p.beat,
                "characters": p.characters,
                "prompt": build_page_prompt(p, ctx.pack),
            }
            for p in plan.pages
        ]
    except (AttributeError, KeyError, RuntimeError):
        prompt_rows = [
            {
                "id": p.id,
                "index": p.index,
                "beat": p.beat,
                "characters": p.characters,
                "prompt": p.prompt,
            }
            for p in plan.pages
        ]
    user = PROMPT_USER_TEMPLATE.format(
        prompts_json=json.dumps(
            prompt_rows,
            ensure_ascii=False,
        )
    )
    resp = ctx.llm.complete(PROMPT_SYSTEM, user)
    ctx.set_cost(estimate_cost(resp))
    data: dict[str, Any] = {}
    try:
        data = _parse_judge_json(resp.content)
        if not isinstance(data, dict):
            data = {}
    except (json.JSONDecodeError, TypeError, ValueError):
        data = {}

    verdicts: list[AuditVerdict] = []
    raw_verdicts = data.get("verdicts") if data else None
    if isinstance(raw_verdicts, list):
        for v in raw_verdicts:
            if not isinstance(v, dict):
                continue
            verdicts.append(
                AuditVerdict(
                    status=_coerce_status(v.get("status", "fail")),
                    notes=v.get("notes", ""),
                    rewrite_suggestion=v.get("rewrite_suggestion", ""),
                )
            )
    if not verdicts:
        verdicts = [AuditVerdict(status="fail", notes="judge parse failure") for _ in plan.pages]

    while len(verdicts) < len(plan.pages):
        verdicts.append(AuditVerdict(status="fail", notes="missing verdict for this page"))
    verdicts = verdicts[: len(plan.pages)]

    for i, v in enumerate(verdicts):
        if i >= len(plan.pages):
            break
        plan.pages[i].audit_status = v.status
        plan.pages[i].audit_notes = v.notes

    rewrites = []
    if isinstance(raw_verdicts, list):
        for v in raw_verdicts:
            if isinstance(v, dict):
                rewrites.append(v.get("rewrite_suggestion", ""))
            else:
                rewrites.append("")
    for i, rw in enumerate(rewrites):
        if i < len(plan.pages) and rw:
            plan.pages[i].prompt = f"{plan.pages[i].prompt}. Correction: {rw}"

    return PromptAuditReport(verdicts=verdicts, vision_model=_judge_model(ctx.pack))


def _audit_single_image(
    ctx: StageContext, asset, path: Path, hero_mode: bool = False, page=None, back_mode: bool = False
) -> AuditVerdict:
    """Judge a single generated image against the main character's
    characterization and the audience. Fail-closed: parse errors and unknown
    statuses become fail. In hero_mode, uses a cover-specific template that
    evaluates full-color composition (not line-art)."""
    character = _main_character(ctx.pack)
    characterization = (
        (character.get("description_en") or character.get("archetype_en") or "")
        if character
        else ""
    )
    audience = getattr(ctx.pack.profile, "audience", "") or getattr(ctx.pack.profile, "pack_type", "") or "the configured audience"
    if not path.exists():
        return AuditVerdict(status="fail", notes="asset file missing")
    img_bytes = path.read_bytes()
    img_b64 = base64.b64encode(img_bytes).decode("ascii")
    if back_mode:
        user = BACK_USER_TEMPLATE.format(audience=audience)
    elif hero_mode:
        user = HERO_USER_TEMPLATE.format(
            characterization=characterization,
            audience=audience,
            expected_title=getattr(asset, "expected_title", "") or "",
            expected_subtitle=getattr(asset, "expected_subtitle", "") or "",
            expected_age_badge=getattr(asset, "expected_age_badge", "") or "",
            expected_author=getattr(asset, "expected_author", "") or "",
        )
    else:
        template = IMAGE_USER_TEMPLATE
        user = template.format(
            index=1,
            characterization=characterization,
            audience=audience,
            beat=getattr(page, "beat", "") or "unspecified",
            characters=", ".join(getattr(page, "characters", []) or []) or "unspecified",
            page_prompt=getattr(page, "prompt", "") or asset.prompt,
        )
    user += '\nLook at the image. Return JSON only: {"status": "ok|warn|fail", "notes": "...", "rewrite_suggestion": "..."}'
    data = _complete_with_image_json(ctx, PROMPT_SYSTEM, user, img_b64, _judge_model(ctx.pack))
    if data is None:
        raw_status = "fail"
        data = {"notes": "judge parse failure"}
    else:
        raw_status = data.get("status", "fail")
    raw_status = _coerce_status(raw_status)
    if raw_status == "warn":
        effective = "fail"
    else:
        effective = raw_status
    return AuditVerdict(
        status=effective,
        notes=data.get("notes", ""),
        cuteness=data.get("cuteness", ""),
        rewrite_suggestion=data.get("rewrite_suggestion", ""),
    )


def _audit_images(ctx: StageContext, assets: AssetPlan) -> AssetAuditReport:
    verdicts: list[AuditVerdict] = []
    character = _main_character(ctx.pack)
    characterization = (
        (character.get("description_en") or character.get("archetype_en") or "")
        if character
        else ""
    )
    audience = getattr(ctx.pack.profile, "audience", "") or getattr(ctx.pack.profile, "pack_type", "") or "the configured audience"
    for i, asset in enumerate(assets.assets):
        path = ctx.assets_dir / f"{asset.id}.png"
        if not path.exists():
            verdicts.append(AuditVerdict(status="fail", notes="asset file missing"))
            continue
        img_bytes = path.read_bytes()
        img_b64 = base64.b64encode(img_bytes).decode("ascii")
        user = (
            IMAGE_USER_TEMPLATE.format(
                index=i + 1,
                characterization=characterization,
                audience=audience,
                beat="",
                characters="",
                page_prompt=asset.prompt,
            )
            + "Look at the image. Return JSON only: {\"status\": \"ok|warn|fail\", \"notes\": \"...\"}"
        )
        data = _complete_with_image_json(ctx, PROMPT_SYSTEM, user, img_b64, _judge_model(ctx.pack))
        if data is None:
            raw_status = "fail"
            data = {"notes": "judge parse failure"}
        else:
            raw_status = data.get("status", "fail")

        # Fail-closed: unknown statuses and parse failures become fail.
        raw_status = _coerce_status(raw_status)

        # Propagate warn as fail: any minor doubt means the page needs to be
        # regenerated. The judge should use "ok" only when it's clearly print-ready.
        if raw_status == "warn":
            effective_status = "fail"
        else:
            effective_status = raw_status

        verdicts.append(
            AuditVerdict(
                status=effective_status,
                notes=data.get("notes", ""),
                cuteness=data.get("cuteness", ""),
            )
        )
        # Store the rewrite suggestion on the asset so the pipeline can
        # regenerate with a fixed prompt.
        if effective_status != "ok" and data.get("rewrite_suggestion"):
            asset.rewrite_suggestion = data.get("rewrite_suggestion", "")
            if not asset.audit_notes:
                asset.audit_notes = data.get("rewrite_suggestion", "")

    # Mark the assets
    for i, v in enumerate(verdicts):
        if i < len(assets.assets):
            assets.assets[i].audit_status = v.status
            if not assets.assets[i].audit_notes:
                assets.assets[i].audit_notes = v.notes

    return AssetAuditReport(verdicts=verdicts, vision_model=_judge_model(ctx.pack))


def _main_character(pack) -> dict | None:
    """Return the pack's main character (role == 'main') from stories.yaml."""
    stories = (getattr(pack, "stories", None) or {})
    if not isinstance(stories, dict):
        return None
    roster = stories.get("characters", [])
    if not isinstance(roster, list):
        return None
    for c in roster:
        if isinstance(c, dict) and c.get("role") == "main":
            return c
    return None


def _audit_character_sheet(ctx: StageContext, character: dict | None = None) -> AuditVerdict:
    """Audit the canonical character reference against its official characterization.

    The sheet is the canonical reference for the whole book (and series), so
    the judge must confirm it matches the characterization before any page is
    generated from it. Franchise catalogs audit the catalog PNG directly; a
    failure is fail-closed (never auto-regenerated from a canonical source).
    Returns a verdict; the pipeline decides whether to regenerate.
    """
    character = character or _main_character(ctx.pack)
    if character is None:
        return AuditVerdict(status="ok", notes="no roster in pack; sheet not audited")

    from productfoundry.series import canonical_character_reference

    char_id = character.get("id", "")
    try:
        sheet_path = canonical_character_reference(ctx.pack, char_id)
    except ValueError:
        from productfoundry.stages.character_sheet import _sheet_path

        sheet_path = _sheet_path(ctx.assets_dir, char_id)
    if not sheet_path.exists():
        return AuditVerdict(status="fail", notes="character sheet missing")

    characterization = (
        character.get("description_en")
        or character.get("archetype_en")
        or character.get("name_en", "the main character")
    )
    palette = character.get("palette_en", "") or ""
    if palette:
        characterization = f"{characterization} Official colors: {palette}."
    user = SHEET_USER_TEMPLATE.format(characterization=characterization)
    img_b64 = base64.b64encode(sheet_path.read_bytes()).decode("ascii")
    data = _complete_with_image_json(ctx, PROMPT_SYSTEM, user, img_b64, _judge_model(ctx.pack))
    if data is None:
        raw_status = "fail"
        data = {"notes": "judge parse failure"}
    else:
        raw_status = data.get("status", "fail")
    raw_status = _coerce_status(raw_status)
    if raw_status == "warn":
        effective = "fail"  # any doubt on the canonical reference → regenerate
    else:
        effective = raw_status
    return AuditVerdict(
        status=effective,
        notes=data.get("notes", ""),
        rewrite_suggestion=data.get("rewrite_suggestion", ""),
    )


class CharacterSheetAuditStage(Stage):
    stage_name = "audit_character_sheet"
    inputs: ClassVar = ["character_sheet"]
    outputs: ClassVar = ["audit_character_sheet"]
    input_models: ClassVar = {"character_sheet": AssetPlan}
    prompt_version = f"{PROMPT_VERSION}-sheet"
    max_regenerations = 2
    gate_verdict = "pass"

    def run(self, ctx: StageContext, character_sheet: AssetPlan) -> AssetAuditReport:
        if not _is_audit_enabled(ctx.pack):
            return AssetAuditReport(
                verdicts=[
                    AuditVerdict(status="ok", notes="audit disabled")
                    for _ in character_sheet.assets
                ],
                vision_model="",
            )
        from productfoundry.stages.character_sheet import (
            CharacterSheetStage,
            _main_sheet_path,
            _roster,
            _sheet_path,
        )

        verdicts: list[AuditVerdict] = []
        for character in _roster(ctx.pack):
            verdict = _audit_character_sheet(ctx, character)
            # Regenerate only a generated (non-canonical) failing sheet, up to
            # max_regenerations attempts. Canonical catalog references are
            # fail-closed: never overwrite the franchise's source of truth.
            for _ in range(self.max_regenerations):
                if verdict.status != "fail":
                    break
                from productfoundry.series import canonical_character_reference

                char_id = character.get("id", "")
                try:
                    canonical_character_reference(ctx.pack, char_id)
                    is_canonical = True
                except ValueError:
                    is_canonical = False
                if is_canonical:
                    break
                sheet_path = _sheet_path(ctx.assets_dir, char_id)
                if sheet_path.exists():
                    sheet_path.unlink()
                if character.get("role") == "main" and _main_sheet_path(ctx.assets_dir).exists():
                    _main_sheet_path(ctx.assets_dir).unlink()
                try:
                    CharacterSheetStage().run(ctx)
                except (OSError, RuntimeError) as e:
                    verdict = AuditVerdict(
                        status="fail",
                        notes=f"sheet regeneration rejected by provider: {e}",
                    )
                    break
                verdict = _audit_character_sheet(ctx, character)
            verdicts.append(verdict)

        for asset, verdict in zip(character_sheet.assets, verdicts):
            asset.audit_status = verdict.status
            asset.audit_notes = verdict.notes
        return AssetAuditReport(verdicts=verdicts, vision_model=_judge_model(ctx.pack))


class PromptAuditStage(Stage):
    stage_name = "audit_prompt"
    inputs: ClassVar = ["concept"]
    outputs: ClassVar = ["audit_prompt"]
    input_models: ClassVar = {"concept": ProductPlan}
    prompt_version = f"{PROMPT_VERSION}-prompt"
    gate_verdict = "pass"
    max_rewrites = 2

    def run(self, ctx: StageContext, concept: ProductPlan) -> PromptAuditReport:
        if not _is_audit_enabled(ctx.pack):
            return PromptAuditReport(
                verdicts=[AuditVerdict(status="ok", notes="audit disabled") for _ in concept.pages],
                vision_model="",
            )
        previous_prompts = [page.prompt for page in concept.pages]
        report = _audit_prompts(ctx, concept)
        for _ in range(self.max_rewrites):
            if report.verdict == "pass":
                break
            if [page.prompt for page in concept.pages] == previous_prompts:
                break
            previous_prompts = [page.prompt for page in concept.pages]
            report = _audit_prompts(ctx, concept)

        # Prompt rewrites are part of the concept contract consumed by assets.
        # Persist the changed plan instead of leaving the next stage with the
        # stale pre-audit artifact.
        env = ctx.get_artifact("concept")
        if env is not None:
            env.artifact["pages"] = [page.model_dump() for page in concept.pages]
            (ctx.artifacts_dir / "concept.json").write_text(env.model_dump_json(indent=2))
            ctx.artifacts["concept"] = env
        return report


class AssetAuditStage(Stage):
    stage_name = "audit_assets"
    inputs: ClassVar = ["assets"]
    outputs: ClassVar = ["audit_assets"]
    input_models: ClassVar = {"assets": AssetPlan}
    prompt_version = f"{PROMPT_VERSION}-image"
    gate_verdict = "pass"

    def run(self, ctx: StageContext, assets: AssetPlan) -> AssetAuditReport:
        if not _is_audit_enabled(ctx.pack):
            return AssetAuditReport(
                verdicts=[AuditVerdict(status="ok", notes="audit disabled") for _ in assets.assets],
                vision_model="",
            )
        # The AssetsStage already audits and retries each page sequentially.
        # This stage is now a pass-through gate: it verifies that every asset
        # passed the judge and persists the verdicts. No re-auditing or
        # regeneration here — that would duplicate vision calls and cost.
        verdicts: list[AuditVerdict] = []
        for a in assets.assets:
            status = a.audit_status if a.audit_status in ("ok", "warn", "fail") else "fail"
            verdicts.append(AuditVerdict(status=status, notes=a.audit_notes))
        # Persist the audit status back into the assets artifact
        env = ctx.get_artifact("assets")
        if env is not None:
            env.artifact["assets"] = [a.model_dump() for a in assets.assets]
            (ctx.artifacts_dir / "assets.json").write_text(env.model_dump_json(indent=2))
        return AssetAuditReport(verdicts=verdicts, vision_model=_judge_model(ctx.pack))
