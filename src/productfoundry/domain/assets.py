"""AssetSpec and AssetPlan — image generation contracts."""
from pydantic import BaseModel, Field


class AssetSpec(BaseModel):
    id: str
    page_id: str
    prompt: str
    aspect_ratio: str = "1:1"
    size: str = "1024x1024"
    quality: str = "high"


class AssetPlan(BaseModel):
    assets: list[AssetSpec] = Field(default_factory=list)
