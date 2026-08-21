"""Packaging — output bundle specifications."""
from pydantic import BaseModel, Field

FormatKind = str  # "digital" | "print"


class PackageSpec(BaseModel):
    format: FormatKind
    language: str
    page_size: str = "8.5x11"  # inches
    bleed_inches: float = 0.0
    marketplace: str = ""


class PackageOutput(BaseModel):
    format: FormatKind
    language: str
    marketplace: str
    path: str
    file_size: int = 0


class PackagePlan(BaseModel):
    packages: list[PackageOutput] = Field(default_factory=list)
