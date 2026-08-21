"""Runtime profile — global provider/model/budget configuration."""
from pathlib import Path
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    provider: str
    model: str = ""
    api_key_env: str = ""
    base_url: str = ""
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
        provider="openai", model="gpt-image-1", api_key_env="OPENAI_API_KEY"
    )
    budget: BudgetConfig = BudgetConfig()


_DEFAULT = Path(__file__).resolve().parents[3] / "runtime" / "default.yaml"


def load_runtime_profile(path: Path | None = None) -> RuntimeProfile:
    p = path or _DEFAULT
    if p.exists():
        import yaml

        return RuntimeProfile.model_validate(yaml.safe_load(p.read_text()) or {})
    return RuntimeProfile()
