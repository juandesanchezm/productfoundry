"""Image generation provider — OpenAI gpt-image-2."""
from __future__ import annotations

import base64
import io
import re

from productfoundry.providers import ImageGenerationRequest, ImageProvider


def _validate_size(size: str) -> str:
    """Validate that the size string is WxH with both dims divisible by 16
    and within GPT Image 2 constraints (max 3840px per side, max 8.3M total px,
    ratio between 1:3 and 3:1)."""
    m = re.match(r"^(\d+)x(\d+)$", size.strip().lower())
    if not m:
        raise ValueError(f"invalid image size format: {size!r} (expected WxH, e.g. 1024x1328)")
    w, h = int(m.group(1)), int(m.group(2))
    if w < 16 or h < 16:
        raise ValueError(f"image dimensions too small: {w}x{h} (min 16)")
    if w > 3840 or h > 3840:
        raise ValueError(f"image dimensions too large: {w}x{h} (max 3840 per side)")
    if w * h > 8_294_400:
        raise ValueError(f"total pixels too large: {w * h} (max 8,294,400)")
    if w / h > 3.0 or h / w > 3.0:
        raise ValueError(f"aspect ratio out of range: {w}x{h} (must be between 1:3 and 3:1)")
    if w % 16 != 0 or h % 16 != 0:
        raise ValueError(f"dimensions must be multiples of 16: {w}x{h}")
    return f"{w}x{h}"


def _validate_image_bytes(data: bytes) -> bytes:
    """Validate that the returned bytes are a valid, non-empty image."""
    if not data:
        raise RuntimeError("provider returned empty image data")
    from PIL import Image

    try:
        buf = io.BytesIO(data)
        with Image.open(buf) as im:
            im.verify()
    except Exception as e:
        raise RuntimeError(f"provider returned invalid image data: {e}") from e
    return data


class PlaceholderImageProvider(ImageProvider):
    def generate(self, request: ImageGenerationRequest) -> bytes:
        # Deterministic ABSTRACT placeholder for smoke tests and offline runs.
        # It is intentionally NOT a character illustration: the real artwork
        # comes from the configured image provider. Render a geometric grid
        # pattern at the requested size so the pipeline can be verified
        # end-to-end (generate → process → package → publish) without relying
        # on any external API.
        import io

        from PIL import Image, ImageDraw

        m = re.match(r"^(\d+)x(\d+)$", request.size.strip().lower())
        if m:
            w, h = int(m.group(1)), int(m.group(2))
        else:
            w, h = 2400, 2400
        im = Image.new("L", (w, h), 255)
        draw = ImageDraw.Draw(im)
        # Diagonal hatched grid: every cell is a uniform parallelogram.
        # No faces, no characters, no pretend artwork.
        cell = max(32, min(w, h) // 12)
        for y in range(0, h, cell):
            draw.line([(0, y), (w, y)], fill=0, width=1)
        for x in range(0, w, cell):
            draw.line([(x, 0), (x, h)], fill=0, width=1)
        for i in range(-h, w + h, cell):
            draw.line([(i, 0), (i + h, h)], fill=0, width=1)
        # Center "PLACEHOLDER" mark
        try:
            from PIL import ImageFont

            fnt = ImageFont.load_default()
            label = "PLACEHOLDER"
            bbox = draw.textbbox((0, 0), label, font=fnt)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            cx, cy = w // 2 - tw // 2, h // 2 - th // 2
            draw.rectangle([cx - 20, cy - 10, cx + tw + 20, cy + th + 10], fill=255)
            draw.text((cx, cy), label, fill=0, font=fnt)
        except (OSError, ValueError):
            pass
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


class OpenAIImageProvider(ImageProvider):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-image-2",
        timeout: float = 120.0,
        max_retries: int = 1,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def _client(self):
        from openai import OpenAI

        return OpenAI(api_key=self.api_key, timeout=self.timeout, max_retries=self.max_retries)

    def generate(self, request: ImageGenerationRequest) -> bytes:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        size = _validate_size(request.size)
        client = self._client()

        common = {
            "model": self.model,
            "prompt": request.prompt,
            "size": size,
            "quality": request.quality,
        }
        if request.output_format:
            common["output_format"] = request.output_format

        refs = request.reference_images or ([request.reference_image] if request.reference_image else [])
        try:
            if refs:
                images = []
                for i, ref_bytes in enumerate(refs):
                    buf = io.BytesIO(ref_bytes)
                    buf.name = f"reference_{i}.png"
                    images.append(buf)
                resp = client.images.edit(image=images, **common)
            else:
                resp = client.images.generate(**common)
        except Exception as exc:
            raise RuntimeError(f"openai image call failed: {exc}") from exc

        if not resp.data:
            raise RuntimeError("provider returned no image data")
        b64 = resp.data[0].b64_json
        if not b64:
            raise RuntimeError("provider returned empty b64_json")
        raw = base64.b64decode(b64)
        data = _validate_image_bytes(raw)
        usage = {}
        if resp.usage is not None:
            usage = (
                resp.usage.model_dump()
                if hasattr(resp.usage, "model_dump")
                else dict(resp.usage)
            )
        request.usage = usage
        return data