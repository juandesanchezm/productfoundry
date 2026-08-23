import httpx

from productfoundry.providers.llm import ollama_chat


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
