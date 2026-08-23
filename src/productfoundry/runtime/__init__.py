"""Runtime profile — global provider/model/budget configuration."""
from pathlib import Path

from pydantic import BaseModel, Field


class QualityEscalation(BaseModel):
    """Per-artifact quality policy: attempts define the quality used on each
    generation attempt. The pipeline escalates only on visual-quality failures,
    not on deterministic failures (wrong size, missing character, etc.).

    Interior pages:  low  -> low  -> medium
    Character sheets: medium -> medium -> high
    Cover/hero:       high -> high -> high
    """

    attempts: list[str] = Field(default_factory=lambda: ["low", "low", "medium"])


class ImageArtifactPolicies(BaseModel):
    """Quality and size policy per artifact type.

    The pack's page_size (e.g. "8.5x11") determines the generation size;
    the runtime only controls quality escalation and whether high is allowed.
    """

    interior: QualityEscalation = QualityEscalation(attempts=["low", "low", "medium"])
    character_sheet: QualityEscalation = QualityEscalation(attempts=["medium", "medium", "high"])
    cover: QualityEscalation = QualityEscalation(attempts=["high", "high", "high"])


class ProviderConfig(BaseModel):
    provider: str
    model: str = ""
    api_key_env: str = ""
    base_url: str = ""
    quality: str = ""  # legacy single quality (overridden by artifact policies)
    extra: dict = Field(default_factory=dict)


class BudgetConfig(BaseModel):
    max_cost: float = 20.0


class RuntimeProfile(BaseModel):
    llm: ProviderConfig = ProviderConfig(
        provider="ollama",
        model="deepseek-v4-flash",
        base_url="https://ollama.com",
        api_key_env="OLLAMA_API_KEY",
    )
    image: ProviderConfig = ProviderConfig(
        provider="openai", model="gpt-image-2", api_key_env="OPENAI_API_KEY", quality="low"
    )
    image_policies: ImageArtifactPolicies = ImageArtifactPolicies()
    budget: BudgetConfig = BudgetConfig()


_DEFAULT = Path(__file__).resolve().parents[3] / "runtime" / "default.yaml"


def load_runtime_profile(path: Path | None = None) -> RuntimeProfile:
    p = path or _DEFAULT
    if p.exists():
        import yaml

        return RuntimeProfile.model_validate(yaml.safe_load(p.read_text()) or {})
    return RuntimeProfile()