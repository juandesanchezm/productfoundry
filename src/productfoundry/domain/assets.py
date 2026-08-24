"""AssetSpec and AssetPlan — image generation contracts."""
from pydantic import BaseModel, Field


class AssetSpec(BaseModel):
    id: str
    page_id: str
    prompt: str
    aspect_ratio: str = "1:1"
    size: str = "1024x1024"
    quality: str = "high"
    audit_status: str = "pending"  # pending | ok | warn | fail
    audit_notes: str = ""
    rewrite_suggestion: str = ""  # judge's single-sentence fix for regeneration
    expected_title: str = ""  # cover copy contract: exact expected text (hero)
    expected_series: str = ""  # cover copy contract: localized series name
    expected_age_badge: str = ""  # cover copy contract: localized age label
    expected_author: str = ""  # cover copy contract: author name
    expected_back_blurb: str = ""  # cover copy contract: back-cover blurb


class AssetPlan(BaseModel):
    assets: list[AssetSpec] = Field(default_factory=list)
