"""Provider abstractions."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel


class ImageProvider(ABC):
    @abstractmethod
    def generate(self, request: "ImageGenerationRequest") -> bytes: ...


class TTSService(ABC):
    @abstractmethod
    def synthesize(self, text: str, voice: str = "alloy") -> bytes: ...


class LLMRequest(BaseModel):
    messages: list[dict[str, str]] = []
    response_schema: type[BaseModel] | None = None
    temperature: float = 0.2


class LLMResponse(BaseModel):
    content: str
    raw: dict[str, Any] = {}


class ImageGenerationRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "1:1"
    size: str = "1024x1024"
    quality: str = "high"
    output_format: str = "png"  # png | jpeg | webp
    reference_image: bytes | None = None  # image-to-image reference (character consistency)
    reference_images: list[bytes] | None = None  # multiple references (per-character sheets)