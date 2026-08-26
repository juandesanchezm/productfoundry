import httpx

from productfoundry.engine.pipeline import build_image_provider
from productfoundry.providers.llm import ollama_chat
from productfoundry.runtime import ProviderConfig, RuntimeProfile


def test_ollama_chat_retries_transient_timeout():
    calls = 0

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def post(self, url, json, headers):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadTimeout("temporary timeout")
            return FakeResponse()

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "{}"}}

    original_client = httpx.Client
    httpx.Client = lambda **kwargs: FakeClient()
    try:
        response = ollama_chat(
            "https://ollama.com",
            "minimax-m3",
            [{"role": "user", "content": "test"}],
        )
    finally:
        httpx.Client = original_client

    assert response.content == "{}"
    assert calls == 2


def test_openai_image_provider_uses_runtime_timeout():
    runtime = RuntimeProfile(
        image=ProviderConfig(provider="openai", model="gpt-image-2", timeout=1200.0)
    )

    provider = build_image_provider(runtime)

    assert provider.timeout == 1200.0
    assert provider.max_retries == 0
