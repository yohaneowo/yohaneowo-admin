"""AI gateway: forwards Claude / Voyage bodies with admin's keys and records the call for the right caller."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.core.dependencies import db_getter
from app.plugin.module_ai_usage.gateway import service
from app.plugin.module_ai_usage.gateway.controller import AiGatewayInternalRouter

BOT = {"X-Bot-Token": "test-bot"}


def test_gateway_forwards_and_records():
    asyncio.run(check_gateway())


async def check_gateway():
    seen: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.anthropic.com":
            body = request.read().decode()
            if "fail" in body:
                return httpx.Response(400, json={"error": {"message": "bad request"}})
            usage = {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 0}
            return httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}], "usage": usage})
        if "limited" in request.read().decode():
            return httpx.Response(429, json={"detail": "rate limited"})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1]}], "usage": {"total_tokens": 42}})

    real_client = httpx.AsyncClient
    upstream = lambda **kwargs: real_client(transport=httpx.MockTransport(provider), **kwargs)  # noqa: E731

    async def no_db():
        yield SimpleNamespace()

    app = FastAPI()
    app.include_router(AiGatewayInternalRouter)
    app.dependency_overrides[db_getter] = no_db
    report = AsyncMock()
    with (
        patch("app.plugin.module_bot.internal_auth.get_bot_settings", return_value=SimpleNamespace(BOT_API_TOKEN="test-bot")),
        patch.object(service, "keys", return_value=SimpleNamespace(ANTHROPIC_API_KEY="sk-test", VOYAGE_API_KEY="pa-test")),
        patch.object(service.httpx, "AsyncClient", upstream),
        patch.object(service.AiCallLogService, "report", report),
        patch("app.plugin.module_ai_usage.gateway.controller.refresh_prices_in_background"),
    ):
        async with real_client(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            caller = {"platform": "discord", "id": "42", "name": "Tester", "trigger": "command"}
            request = {"project": "yohaneowo-bot", "feature": "movie-ask", "caller": caller, "body": {"model": "claude-test", "messages": []}}
            assert (await client.post("/internal/ai/messages", json=request)).status_code == 401
            answer = (await client.post("/internal/ai/messages", headers=BOT, json=request)).json()["data"]
            assert answer["status"] == 200 and answer["body"]["content"][0]["text"] == "hi"
            assert seen[-1].headers["x-api-key"] == "sk-test"
            log = report.await_args.args[0].logs[0]
            assert (log.provider, log.model, log.feature, log.user_platform, log.user_external_id) == ("anthropic", "claude-test", "movie-ask", "discord", "42")
            assert (log.input_tokens, log.output_tokens, log.cache_read_tokens, log.success) == (120, 30, 5, True)

            # Provider errors are passed back and still recorded (they are billed).
            failing = {**request, "body": {"model": "claude-test", "messages": [], "note": "fail"}}
            answer = (await client.post("/internal/ai/messages", headers=BOT, json=failing)).json()["data"]
            assert answer["status"] == 400
            log = report.await_args.args[0].logs[0]
            assert not log.success and "bad request" in log.error_message

            # Voyage: recorded with total tokens; a 429 is returned for the caller to wait out, not recorded.
            embed = {"project": "yohaneowo-bot", "feature": "movie-embedding", "body": {"model": "voyage-test", "input": ["x"]}}
            answer = (await client.post("/internal/ai/embeddings", headers=BOT, json=embed)).json()["data"]
            assert answer["status"] == 200
            log = report.await_args.args[0].logs[0]
            assert (log.provider, log.input_tokens, log.user_platform) == ("voyage", 42, "system")
            calls = report.await_count
            limited = {**embed, "body": {"model": "voyage-test", "input": ["limited"]}}
            assert (await client.post("/internal/ai/embeddings", headers=BOT, json=limited)).json()["data"]["status"] == 429
            assert report.await_count == calls

        with patch.object(service, "keys", return_value=SimpleNamespace(ANTHROPIC_API_KEY="", VOYAGE_API_KEY="")):
            async with real_client(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.post("/internal/ai/messages", headers=BOT, json=request)).status_code == 503
