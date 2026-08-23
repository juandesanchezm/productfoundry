"""PackProfile — niche configuration for a product category."""
from pydantic import BaseModel, Field

FormatType = str  # e.g. "digital", "print" — freeform, declared per pack
Marketplace = str  # freeform marketplace identifier declared by the pack


class FormatSpec(BaseModel):
    marketplaces: list[Marketplace] = Field(default_factory=list)


class PackFormats(BaseModel):
    digital: FormatSpec = FormatSpec()
    print: FormatSpec = FormatSpec()


class PackProfile(BaseModel):
    id: str
    schema_version: int = 1
    pack_version: int = 1
    pack_type: str = "generic"  # freeform identifier declared per pack
    default_language: str = "en"
    languages: list[str] = Field(default_factory=lambda: ["en", "es"])
    formats: PackFormats = PackFormats()
    page_count: int = 24
    image_size: str = "1024x1024"
    page_size: str = "8.5x8.5"  # trim size in inches (WxH), e.g. "8.5x8.5"
    author: str = "Juande Sánchez"  # author shown on cover
    audience: str = ""  # freeform description of the target audience
    age_range: str = ""  # e.g. "3-8" — shown as "Ages 3-8" badge on the cover
    series_name: str | dict[str, str] = ""  # franchise branding: plain string or {lang: name} mapping


def derive_generation_size(page_size: str, base_px: int = 1024) -> str:
    """Derive a GPT Image 2 generation size from a pack's trim page_size.

    The generation size should match the aspect ratio of the trim so the
    deterministic upscale in postprocess preserves proportions. Both dims
    are rounded to the nearest multiple of 16 (GPT Image 2 requirement).

    Example: "8.5x11" (ratio 0.773) -> "1024x1328" (ratio 0.771)
    """
    try:
        w_in, h_in = map(float, page_size.lower().split("x"))
    except ValueError:
        return f"{base_px}x{base_px}"
    ratio = w_in / h_in
    if ratio >= 1.0:
        gen_w = base_px
        gen_h = round(base_px / ratio / 16) * 16
    else:
        gen_h = base_px
        gen_w = round(base_px * ratio / 16) * 16
    gen_w = max(16, gen_w)
    gen_h = max(16, gen_h)
    return f"{gen_w}x{gen_h}"
