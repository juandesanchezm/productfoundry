"""Cost tracking — estimate per-call cost for image and LLM providers.

The estimator prefers real provider usage when available (GPT Image 2 returns
token counts); otherwise it falls back to the static pricing tables in
``pricing.py``. ``estimate_image_cost`` is the single entry point used by
the asset / hero / back-cover stages.
"""
from __future__ import annotations

from typing import Any

from productfoundry.providers.pricing import image_cost_usd


def estimate_image_cost(
    provider: str,
    model: str,
    size: str,
    quality: str,
    usage: dict[str, Any] | None = None,
) -> float:
    """Return the cost of one image generation call.

    Prefers real ``usage`` tokens when the provider reports them; falls back
    to the static per-image pricing tables otherwise.
    """
    if usage:
        tokens = usage.get("total_tokens") or (
            (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        )
        if tokens and model.startswith("gpt-image-"):
            # Token-based pricing per the OpenAI Image docs (2026-04). The
            # conservative estimate is $0.000013 per token; the actual rate
            # depends on quality but is in the same order of magnitude.
            return float(tokens) * 0.000013
    return image_cost_usd(provider, model, size, quality)


def estimate_llm_cost(raw: dict[str, Any] | None) -> float:
    """Estimate LLM call cost from prompt_eval_count/eval_count (Ollama format)."""
    if not raw:
        return 0.0
    tokens = int(raw.get("prompt_eval_count", 0)) + int(raw.get("eval_count", 0))
    return tokens * 2e-8  # matches the legacy TOKEN_COST_PER_TOKEN constant.