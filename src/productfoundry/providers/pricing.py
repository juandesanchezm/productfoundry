"""Image generation pricing — per-provider cost tables used for budget tracking.

Costs are USD per generated image. Unknown providers/models raise an error
rather than returning $0.00, so the pipeline never silently bypasses the budget.
"""
from __future__ import annotations

# gpt-image-1 (OpenAI) — USD per image, by quality and size.
_OPENAI_GPT_IMAGE_1: dict[str, dict[str, float]] = {
    "low": {"1024x1024": 0.011, "1024x1536": 0.016, "1536x1024": 0.016},
    "medium": {"1024x1024": 0.042, "1024x1536": 0.063, "1536x1024": 0.063},
    "high": {"1024x1024": 0.167, "1024x1536": 0.25, "1536x1024": 0.25},
}

# gpt-image-1-mini (OpenAI) — cost-efficient version, same endpoints.
_OPENAI_GPT_IMAGE_1_MINI: dict[str, dict[str, float]] = {
    "low": {"1024x1024": 0.005, "1024x1536": 0.006, "1536x1024": 0.006},
    "medium": {"1024x1024": 0.011, "1024x1536": 0.015, "1536x1024": 0.015},
    "high": {"1024x1024": 0.036, "1024x1536": 0.052, "1536x1024": 0.052},
}

# gpt-image-2 (OpenAI) — token-based pricing; these are approximate per-image
# costs for common sizes. GPT Image 2 supports flexible resolutions, so we
# interpolate between known sizes for arbitrary WxH within the supported range.
_OPENAI_GPT_IMAGE_2: dict[str, dict[str, float]] = {
    "low": {"1024x1024": 0.006, "1024x1536": 0.005, "1024x1328": 0.005},
    "medium": {"1024x1024": 0.053, "1024x1536": 0.041, "1024x1328": 0.041},
    "high": {"1024x1024": 0.211, "1024x1536": 0.165, "1024x1328": 0.165},
}

_SUPPORTED_MODELS = {
    "gpt-image-1": _OPENAI_GPT_IMAGE_1,
    "gpt-image-1-mini": _OPENAI_GPT_IMAGE_1_MINI,
    "gpt-image-2": _OPENAI_GPT_IMAGE_2,
}


def image_cost_usd(provider: str, model: str, size: str, quality: str) -> float:
    """Return the estimated USD cost of a single image generation.

    Raises ValueError for unknown provider/model so the pipeline fails-closed
    rather than silently budgeting $0.00.
    """
    size = size.lower()
    quality = (quality or "high").lower()
    if provider == "openai":
        table = _SUPPORTED_MODELS.get(model)
        if table is None:
            raise ValueError(f"unknown image model: {model!r} (supported: {list(_SUPPORTED_MODELS)})")
        quality_table = table.get(quality)
        if quality_table is None:
            raise ValueError(f"unknown quality {quality!r} for model {model!r}")
        cost = quality_table.get(size)
        if cost is not None:
            return cost
        # Interpolate by nearest known size for flexible-resolution models
        if model == "gpt-image-2":
            return _interpolate_cost(quality_table, size)
        raise ValueError(f"unknown size {size!r} for {model}/{quality}")
    if provider == "placeholder":
        return 0.0
    raise ValueError(f"unknown image provider: {provider!r} (supported: openai, placeholder)")


def _interpolate_cost(quality_table: dict[str, float], size: str) -> float:
    """Approximate cost for a flexible-resolution size by nearest known size."""
    try:
        w, h = map(int, size.split("x"))
    except ValueError:
        raise ValueError(f"cannot parse size {size!r}")
    total_px = w * h
    best_cost = 0.0
    best_diff = float("inf")
    for known_size, cost in quality_table.items():
        kw, kh = map(int, known_size.split("x"))
        diff = abs((kw * kh) - total_px)
        if diff < best_diff:
            best_diff = diff
            best_cost = cost
    return best_cost