"""Listing — SEO metadata per marketplace, format and language."""
from pydantic import BaseModel, Field


class Listing(BaseModel):
    marketplace: str
    language: str
    format: str = ""  # "digital" | "print"
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    price: float = 0.0
    category: str = ""
    ai_disclosure: str = ""


class ListingSet(BaseModel):
    listings: list[Listing] = Field(default_factory=list)