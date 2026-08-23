"""LLM provider — Ollama-compatible chat completion with JSON parsing."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel

from productfoundry.providers import LLMResponse


class ParseError(Exception):
    pass


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def parse_structured(schema: type[BaseModel], text: str) -> BaseModel:
    """Parse LLM text into a Pydantic schema, tolerating code fences."""
    cleaned = _strip_json_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ParseError(f"invalid json: {e}") from e
    return schema.model_validate(data)


def ollama_chat(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    api_key: str = "",
    format_json: bool = True,
    timeout: float = 60.0,
) -> LLMResponse:
    """Call an Ollama-compatible /api/chat endpoint."""
    url = base_url.rstrip("/") + "/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if format_json:
        payload["format"] = "json"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    for attempt in range(2):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            break
        except httpx.TimeoutException:
            if attempt == 1:
                raise

    message = data.get("message", {})
    content = message.get("content", "")
    return LLMResponse(content=content, raw=data)


def openai_chat(
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    response_format: dict | None = None,
    timeout: float = 60.0,
) -> LLMResponse:
    """Call OpenAI's /v1/chat/completions endpoint."""
    url = "https://api.openai.com/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if response_format:
        payload["response_format"] = response_format
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    choice = data.get("choices", [{}])[0]
    content = choice.get("message", {}).get("content", "")
    return LLMResponse(content=content, raw=data)


class PlaceholderLLMClient:
    """Deterministic stub LLM for tests and offline smoke runs.

    Returns a small canned JSON response that satisfies the concept and listing
    schemas. Ignores all prompt content. The main character id is read from
    the pack roster (never hardcoded) so the engine stays agnostic.
    """

    def __init__(self, schema_hint: str = "concept", pack=None) -> None:
        self.schema_hint = schema_hint
        self._main_id = ""
        if pack is not None:
            stories = getattr(pack, "stories", None) or {}
            roster = stories.get("characters", []) if isinstance(stories, dict) else []
            for c in roster:
                if isinstance(c, dict) and c.get("role") == "main" and c.get("id"):
                    self._main_id = c["id"]
                    break

    def complete(self, system: str, user: str) -> LLMResponse:
        if "Audit each prompt" in user or "storytelling judge" in user.lower():
            # Prompt audit: one ok verdict per prompt line
            import re

            count = len(re.findall(r'"id":\s*"page_', user)) or 1
            payload = {
                "verdicts": [
                    {"status": "ok", "notes": "", "rewrite_suggestion": ""} for _ in range(count)
                ]
            }
        elif "listing" in self.schema_hint or "listings" in user.lower():
            payload = {
                "listings": [
                    {
                        "marketplace": "digital-a",
                        "language": "en",
                        "format": "digital",
                        "title": "Placeholder Digital EN",
                        "description": "Placeholder description.",
                        "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"],
                        "price": 4.99,
                        "category": "placeholder",
                    },
                    {
                        "marketplace": "digital-a",
                        "language": "es",
                        "format": "digital",
                        "title": "Placeholder Digital ES",
                        "description": "Descripción placeholder.",
                        "tags": ["t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8"],
                        "price": 4.99,
                        "category": "placeholder",
                    },
                ]
            }
        else:
            import re

            m = re.search(r"Número de páginas:\s*(\d+)", user)
            count = int(m.group(1)) if m else 3
            main_id = self._main_id or "main"
            payload = {
                "pages": [
                    {
                        "id": f"page_{i + 1:03d}",
                        "index": i + 1,
                        "prompt": f"placeholder page {i + 1}",
                        "title": f"Page {i + 1}",
                        "characters": [main_id],
                    }
                    for i in range(count)
                ],
                "titles": {"en": "Placeholder EN", "es": "Placeholder ES"},
                "subtitle": "placeholder subtitle",
                "description_hint": "placeholder description hint",
            }
        return LLMResponse(content=json.dumps(payload), raw={"prompt_eval_count": 1, "eval_count": 1})

    def complete_with_image(self, system: str, user: str, image_b64: str, model: str | None = None) -> LLMResponse:
        """Vision stub: always approves (used by the smoke runtime)."""
        payload = {"status": "ok", "notes": "", "rewrite_suggestion": ""}
        return LLMResponse(content=json.dumps(payload), raw={"prompt_eval_count": 1, "eval_count": 1})
