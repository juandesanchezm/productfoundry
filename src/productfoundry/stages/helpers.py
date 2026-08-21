"""Stage helpers — retry parse, cost estimation."""
from __future__ import annotations
from pydantic import BaseModel, ValidationError

from productfoundry.providers import LLMResponse
from productfoundry.providers.llm import ParseError, parse_structured

TOKEN_COST_PER_TOKEN = 0.00000002


def estimate_cost(response: LLMResponse) -> float:
    raw = response.raw or {}
    tokens = int(raw.get("prompt_eval_count", 0)) + int(raw.get("eval_count", 0))
    return tokens * TOKEN_COST_PER_TOKEN


def retry_parse(llm, system: str, user: str, schema: type[BaseModel], max_retries: int = 2, on_response=None) -> BaseModel:
    attempts = 0
    while True:
        response = llm.complete(system=system, user=user)
        attempts += 1
        if on_response is not None:
            on_response(response)
        try:
            return parse_structured(schema, response.content)
        except (ParseError, ValidationError) as e:
            if attempts >= max_retries:
                raise
            system = f"El JSON anterior no es válido: {e}. Devuelve SOLO JSON válido"
