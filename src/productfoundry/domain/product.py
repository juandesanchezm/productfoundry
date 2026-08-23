"""ProductRequest and ProductPlan — a production instance and its concept output."""
from pydantic import BaseModel, Field


class Character(BaseModel):
    """A named character in a story. Names are identical across languages
    (franchise-style, like Coco Wyo / Disney) so the same character can be
    reused across volumes of a series."""

    id: str
    role: str = "supporting"  # main | supporting
    name_en: str = ""
    name_es: str = ""
    archetype_en: str = ""
    archetype_es: str = ""
    description_en: str = ""
    description_es: str = ""
    palette_en: str = ""  # canonical colors (e.g. "chocolate brown body, cream belly")
    palette_es: str = ""


class ProductRequest(BaseModel):
    pack: str
    theme: str
    page_count: int = 24
    languages: list[str] = Field(default_factory=lambda: ["en", "es"])
    formats: list[str] = Field(default_factory=lambda: ["digital", "print"])
    title_hint: str = ""
    story_id: str = ""  # optional: look up in pack.stories
    character: str = ""  # freeform protagonist descriptor for character consistency
    franchise: str = ""  # catalog layout: franchise directory (e.g. "cocholate")
    series: str = ""  # catalog layout: series id inside the franchise
    book: str = ""  # catalog layout: book id inside the series


class PageSpec(BaseModel):
    id: str
    index: int
    prompt: str
    title: str = ""
    theme: str = ""
    beat: str = ""  # narrative beat (when in story mode)
    characters: list[str] = Field(default_factory=list)  # roster IDs present on this page
    audit_status: str = "pending"  # pending | ok | warn | fail
    audit_notes: str = ""


class ProductPlan(BaseModel):
    pack_id: str
    pack_version: int
    theme: str
    pages: list[PageSpec] = Field(default_factory=list)
    titles: dict[str, str] = Field(default_factory=dict)  # language -> title
    subtitle: str = ""
    description_hint: str = ""
