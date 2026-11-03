"""The 10 compatibility checks from design doc §9.1 using the official OpenAI SDK."""

from __future__ import annotations

import asyncio
import httpx
import pytest

from api_support import make_config, mint
from brahma_api.server import create_app

openai = pytest.importorskip("openai")


@pytest.fixture
def api_setup(tmp_path):
    config = make_config(tmp_path)
    # Ensure workspace exists
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    _rec, token = mint(config, "reader")
    app = create_app(config)
    return config, token, app


def test_sdk_models_list(api_setup):
    _config, token, app = api_setup

    async def _test():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http_client:
            client = openai.AsyncOpenAI(base_url="http://testserver/v1", api_key=token, http_client=http_client)
            models_page = await client.models.list()
            ids = [m.id for m in models_page.data]
            assert "brahma-evo" in ids
            assert "brahma-evo:read" in ids
            assert "brahma-evo:plan" in ids

    asyncio.run(_test())


def test_sdk_non_streaming_chat(api_setup):
    _config, token, app = api_setup

    async def _test():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http_client:
            client = openai.AsyncOpenAI(base_url="http://testserver/v1", api_key=token, http_client=http_client)
            completion = await client.chat.completions.create(
                model="brahma-evo",
                messages=[{"role": "user", "content": "hello"}],
            )
            assert completion.choices[0].finish_reason == "stop"
            assert "[stub provider]" in completion.choices[0].message.content

    asyncio.run(_test())


def test_sdk_streaming_chat(api_setup):
    _config, token, app = api_setup

    async def _test():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http_client:
            client = openai.AsyncOpenAI(base_url="http://testserver/v1", api_key=token, http_client=http_client)
            stream = await client.chat.completions.create(
                model="brahma-evo",
                messages=[{"role": "user", "content": "hello"}],
                stream=True,
                stream_options={"include_usage": True},
            )
            collected_chunks = []
            full_text = ""
            saw_empty_choices = False

            async for chunk in stream:
                collected_chunks.append(chunk)
                if chunk.choices and chunk.choices[0].delta.content:
                    full_text += chunk.choices[0].delta.content
                if len(chunk.choices) == 0:
                    saw_empty_choices = True

            assert "[stub provider]" in full_text
            assert saw_empty_choices is True

    asyncio.run(_test())


def test_sdk_unknown_model_raises_not_found(api_setup):
    _config, token, app = api_setup

    async def _test():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http_client:
            client = openai.AsyncOpenAI(base_url="http://testserver/v1", api_key=token, http_client=http_client)
            with pytest.raises(openai.NotFoundError) as exc_info:
                await client.chat.completions.create(
                    model="brahma-evo:unknown",
                    messages=[{"role": "user", "content": "hi"}],
                )
            assert exc_info.value.code == "model_not_found"

    asyncio.run(_test())


def test_sdk_bad_token_raises_authentication_error(api_setup):
    _config, _token, app = api_setup

    async def _test():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http_client:
            client = openai.AsyncOpenAI(base_url="http://testserver/v1", api_key="bad-token", http_client=http_client)
            with pytest.raises(openai.AuthenticationError) as exc_info:
                await client.chat.completions.create(
                    model="brahma-evo",
                    messages=[{"role": "user", "content": "hi"}],
                )
            assert exc_info.value.code == "invalid_api_key"

    asyncio.run(_test())


def test_sdk_unsupported_n_raises_bad_request(api_setup):
    _config, token, app = api_setup

    async def _test():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http_client:
            client = openai.AsyncOpenAI(base_url="http://testserver/v1", api_key=token, http_client=http_client)
            with pytest.raises(openai.BadRequestError) as exc_info:
                await client.chat.completions.create(
                    model="brahma-evo",
                    messages=[{"role": "user", "content": "hi"}],
                    n=2,
                )
            assert exc_info.value.param == "n"

    asyncio.run(_test())


def test_sdk_concurrency_429_raises_rate_limit(tmp_path):
    config = make_config(tmp_path)
    config.limits.concurrent_runs = 1
    config.limits.concurrent_runs_per_token = 1
    _rec, token = mint(config, "reader")
    app = create_app(config)

    async def _test():
        limiter = app.state.limiter
        await limiter._global_sem.acquire()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http_client:
                # Client with max_retries=0 to observe 429 directly
                client = openai.AsyncOpenAI(
                    base_url="http://testserver/v1",
                    api_key=token,
                    http_client=http_client,
                    max_retries=0,
                )
                with pytest.raises(openai.RateLimitError) as exc_info:
                    await client.chat.completions.create(
                        model="brahma-evo",
                        messages=[{"role": "user", "content": "hi"}],
                    )
                assert exc_info.value.response.status_code == 429
                assert exc_info.value.response.headers.get("retry-after") == "5"
        finally:
            limiter._global_sem.release()

    asyncio.run(_test())
