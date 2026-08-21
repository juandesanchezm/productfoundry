"""ProductRequest and ProductPlan — a production instance and its concept output."""
from pydantic import BaseModel, Field


class ProductRequest(BaseModel):
    pack: str
    theme: str
    page_count: int = 30
    languages: list[str] = Field(default_factory=lambda: ["en", "es"])
    formats: list[str] = Field(default_factory=lambda: ["digital", "print"])
    title_hint: str = ""


class PageSpec(BaseModel):
    id: str
    index: int
    prompt: str
    title: str = ""
    theme: str = ""


class ProductPlan(BaseModel):
    pack_id: str
    pack_version: int
    theme: str
    pages: list[PageSpec] = Field(default_factory=list)
    titles: dict[str, str] = Field(default_factory=dict)  # language -> title
    subtitle: str = ""
    description_hint: str = ""
