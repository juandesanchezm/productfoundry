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
    page_count: int = 30
    image_size: str = "1024x1024"
