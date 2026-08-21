"""Image generation provider — OpenAI gpt-image."""
from __future__ import annotations
import base64

from productfoundry.providers import ImageGenerationRequest, ImageProvider


def _size_to_pixels(size: str) -> str:
    return size  # already in "WxH" format


class PlaceholderImageProvider(ImageProvider):
    def generate(self, request: ImageGenerationRequest) -> bytes:
        # Return a 1x1 transparent PNG when no provider is configured
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        )


class OpenAIImageProvider(ImageProvider):
    def __init__(self, api_key: str, model: str = "gpt-image-1") -> None:
        self.api_key = api_key
        self.model = model

    def generate(self, request: ImageGenerationRequest) -> bytes:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        resp = client.images.generate(
            model=self.model,
            prompt=request.prompt,
            size=_size_to_pixels(request.size),
            quality=request.quality,
        )
        b64 = resp.data[0].b64_json
        return base64.b64decode(b64)
