"""Movie API authorization, snapshot leases and migration safety on isolated SQLite."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.dependencies import db_getter
from app.plugin.module_bot.movie import service
from app.plugin.module_bot.movie.controller import MovieInternalRouter, MovieWebsiteRouter
from app.plugin.module_bot.movie.model import MovieCacheModel, MovieRecordModel, MovieSubscriberModel
from app.plugin.module_bot.movie.sources import base, omdb, tmdb
from app.plugin.module_bot.movie.sync import job as sync_job
from app.plugin.module_external.model import ExternalApiProviderModel, ExternalApiUsageModel


def test_movie_lifecycle():
    asyncio.run(check_lifecycle())


async def check_lifecycle():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for model in (MovieSubscriberModel, MovieRecordModel, MovieCacheModel):
            await conn.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session():
        async with factory.begin() as db:
            yield db

    app = FastAPI()
    app.include_router(MovieInternalRouter)
    app.include_router(MovieWebsiteRouter)
    app.dependency_overrides[db_getter] = session
    # The sync loop would reach the real database; these tests drive the sync steps directly.
    app.dependency_overrides[sync_job.ensure_loop] = lambda: None
    bot = {"X-Bot-Token": "test-bot"}
    site = {"X-Movies-Token": "test-site"}
    uid = "123456789012345678"
    sub = f"/internal/movies/subscribers/{uid}"
    view = f"/website/movies/{uid}"
    payload = {"traktUser": "test-user", "traktName": "Test", "region": "TW"}
    with (
        patch("app.plugin.module_bot.internal_auth.get_bot_settings", return_value=SimpleNamespace(BOT_API_TOKEN="test-bot")),
        patch("app.plugin.module_bot.movie.auth.site_token", return_value="test-site"),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(view)).status_code == 401
            assert (await client.put(sub, headers=site, json=payload)).status_code == 401
            assert (await client.get(view, headers=site)).json()["data"]["subscription"] is None
            assert (await client.put(sub, headers=bot, json=payload)).status_code == 200
            async with factory.begin() as db:
                job = await service.claim(db)
            assert job["userId"] == uid
            async with factory.begin() as db:
                assert await service.claim(db) is None
            snapshot = {
                "version": 1,
                "ratingCount": 0,
                "watchedCount": 0,
                "plays": 0,
                "mean": None,
                "showRatingCount": 0,
                "showMean": None,
                "distribution": [0] * 10,
                "sampleCount": 0,
                "sampleLimit": 200,
                "genres": [],
                "people": [],
            }
            stored = {**snapshot, "films": [], "ratedIds": [], "depth": None, "depthGroups": 0}
            taste = {"version": 1, "mean": 7, "seenIds": [], "ratings": [], "genres": [], "people": [], "historyItems": []}
            # A stale lease (subscription changed, or another sync took over) is dropped.
            async with factory.begin() as db:
                assert not await service.finish_sync(db, uid, "stale", job["leaseId"], stored, taste)
            assert (await client.get(f"/internal/movies/taste/{uid}", headers=bot)).json()["data"] is None
            async with factory.begin() as db:
                assert await service.finish_sync(db, uid, job["revision"], job["leaseId"], stored, taste)
            saved_view = (await client.get(view, headers=site)).json()["data"]
            assert saved_view["snapshot"] == stored
            assert (await client.get(f"/internal/movies/taste/{uid}", headers=bot)).json()["data"]["taste"] == taste
            # The only user with a count is at the top of the platform.
            assert saved_view["platform"] == {"watchedPercentile": 100, "users": 1, "showsPercentile": None, "showUsers": 0, "animePercentile": None, "animeUsers": 0}
            assert (await client.post(view + "/refresh", headers=site)).status_code == 429
            record = f"/internal/movies/records/{uid}/42"
            await client.patch(record, headers=bot, json={"data": {"score": 9}})
            await client.patch(record, headers=bot, json={"data": {"feedback": "watch"}})
            saved = (await client.get(f"/internal/movies/records/{uid}", headers=bot)).json()["data"]
            assert saved["42"] == {"score": 9, "feedback": "watch"}
            await client.delete(sub, headers=bot)
            assert (await client.get(view, headers=site)).json()["data"]["snapshot"] is None
            assert (await client.get(view, headers=site)).json()["data"]["platform"] is None
            assert (await client.patch(record, headers=bot, json={"revision": job["revision"], "data": {"score": 2}})).status_code == 409
            imported = await client.post(f"/internal/movies/imports/subscribers/{uid}", headers=bot, json=payload)
            assert imported.json()["data"]["created"] is False
            assert (await client.get(sub, headers=bot)).json()["data"] is None
    await engine.dispose()


def test_omdb_parse():
    body = {
        "Response": "True",
        "imdbRating": "8.1",
        "imdbVotes": "1,234,567",
        "Metascore": "N/A",
        "Ratings": [{"Source": "Rotten Tomatoes", "Value": "87%"}, {"Source": "Metacritic", "Value": "74/100"}],
    }
    assert omdb.parse(body) == {"imdbRating": 8.1, "imdbVotes": 1234567, "rottenTomatoes": 87, "metacritic": 74, "year": None}
    assert omdb.parse({**body, "Year": "2019–2021"})["year"] == 2019
    assert omdb.parse({"Response": "True", "imdbRating": "N/A", "imdbVotes": "N/A"})["imdbRating"] is None
    assert omdb.parse({"Response": "False", "Error": "Movie not found!"})["imdbVotes"] is None


async def source_app():
    """An app with the movie internal routes on an in-memory database that has the usage tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for model in (MovieCacheModel, ExternalApiProviderModel, ExternalApiUsageModel):
            await conn.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session():
        async with factory.begin() as db:
            yield db

    app = FastAPI()
    app.include_router(MovieInternalRouter)
    app.dependency_overrides[db_getter] = session
    # The sync loop would reach the real database; these tests drive the sync steps directly.
    app.dependency_overrides[sync_job.ensure_loop] = lambda: None
    return engine, factory, app


async def usage_row(factory, provider):
    async with factory() as db:
        rows = list(await db.scalars(select(ExternalApiUsageModel).where(ExternalApiUsageModel.provider == provider)))
        return rows[0] if rows else None


async def set_limit(factory, provider, limit):
    async with factory.begin() as db:
        row = await db.get(ExternalApiProviderModel, provider)
        row.daily_limit = limit


BOT = {"X-Bot-Token": "test-bot"}
BOT_SETTINGS = SimpleNamespace(BOT_API_TOKEN="test-bot")


def test_ratings_are_cached_and_counted():
    asyncio.run(check_ratings())


async def check_ratings():
    engine, factory, app = await source_app()
    scores = {"imdbRating": 7.5, "imdbVotes": 900, "rottenTomatoes": None, "metacritic": 61}
    answers = {
        "tt0000001": (scores, None, None),
        "tt0000002": (None, None, "ReadTimeout (tt0000002)"),
        "tt0000003": (None, "Request limit reached!", None),
    }
    fetch = AsyncMock(side_effect=lambda client, imdb_id: answers[imdb_id])
    with (
        patch("app.plugin.module_bot.internal_auth.get_bot_settings", return_value=BOT_SETTINGS),
        patch.object(omdb, "omdb_key", return_value="test-key"),
        patch.object(omdb, "_fetch", fetch),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            ratings = "/internal/movies/ratings"
            assert (await client.post(ratings, headers=BOT, json={"imdbIds": ["bad"]})).status_code == 422
            body = {"imdbIds": ["tt0000001", "tt0000002"]}
            # A failed lookup is left out and not cached; the other is.
            assert (await client.post(ratings, headers=BOT, json=body)).json()["data"] == {"tt0000001": scores}
            used = await usage_row(factory, "omdb")
            assert (used.requests, used.cache_hits, used.failures) == (2, 0, 1)
            assert (await client.post(ratings, headers=BOT, json=body)).json()["data"] == {"tt0000001": scores}
            assert fetch.await_count == 3
            used = await usage_row(factory, "omdb")
            assert (used.requests, used.cache_hits) == (3, 1)

            # Only what is left of the daily limit is fetched; the rest waits for a later sync.
            await set_limit(factory, "omdb", 4)
            await client.post(ratings, headers=BOT, json={"imdbIds": ["tt0000002", "tt0000003"]})
            assert fetch.await_count == 4

            # OMDb's own refusal stops the provider for the day, whatever the limit says.
            await set_limit(factory, "omdb", 100)
            await client.post(ratings, headers=BOT, json={"imdbIds": ["tt0000003"]})
            assert fetch.await_count == 5
            async with factory() as db:
                assert (await db.get(ExternalApiProviderModel, "omdb")).blocked_reason == "Request limit reached!"
            await client.post(ratings, headers=BOT, json={"imdbIds": ["tt0000002"]})
            assert fetch.await_count == 5
    await engine.dispose()


def test_tmdb_proxy_forwards_status_and_respects_quota():
    asyncio.run(check_tmdb_proxy())


async def check_tmdb_proxy():
    engine, factory, app = await source_app()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/movie/1"):
            return httpx.Response(200, json={"id": 1, "title": "One"})
        return httpx.Response(404, json={"status_message": "not found"})

    real_client = httpx.AsyncClient
    upstream = lambda **kwargs: real_client(transport=httpx.MockTransport(answer), **kwargs)  # noqa: E731
    with (
        patch("app.plugin.module_bot.internal_auth.get_bot_settings", return_value=BOT_SETTINGS),
        patch.object(tmdb, "token", return_value="v3-key"),
        patch.object(base.httpx, "AsyncClient", upstream),
    ):
        async with real_client(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            proxy = "/internal/movies/sources/tmdb"
            assert (await client.post(proxy, headers=BOT, json={"path": "/account/1"})).status_code == 422
            found = (await client.post(proxy, headers=BOT, json={"path": "/movie/1", "params": {"language": "zh-TW"}})).json()["data"]
            assert found == {"status": 200, "body": {"id": 1, "title": "One"}}
            # TMDB's own 404 is passed on, and is not counted as a failure.
            missing = (await client.post(proxy, headers=BOT, json={"path": "/movie/2"})).json()["data"]
            assert missing["status"] == 404
            used = await usage_row(factory, "tmdb")
            assert (used.requests, used.failures) == (2, 0)
            await set_limit(factory, "tmdb", 2)
            assert (await client.post(proxy, headers=BOT, json={"path": "/movie/1"})).status_code == 503
    await engine.dispose()


def test_old_films_keep_their_scores_longer():
    asyncio.run(check_cache_lifetimes())


async def check_cache_lifetimes():
    from datetime import timedelta

    engine, factory, app = await source_app()
    ten_days_ago = (service.utcnow() - timedelta(days=10)).isoformat()
    scores = {"imdbRating": 7.0, "imdbVotes": 1, "rottenTomatoes": None, "metacritic": None}
    async with factory.begin() as db:
        db.add(MovieCacheModel(namespace="omdb", cache_key="tt0000010", data={**scores, "year": 1999, "fetchedAt": ten_days_ago}))
        db.add(MovieCacheModel(namespace="omdb", cache_key="tt0000011", data={**scores, "year": service.utcnow().year, "fetchedAt": ten_days_ago}))
    fetch = AsyncMock(return_value=({**scores, "year": 2026}, None, None))
    with (
        patch("app.plugin.module_bot.internal_auth.get_bot_settings", return_value=BOT_SETTINGS),
        patch.object(omdb, "omdb_key", return_value="test-key"),
        patch.object(omdb, "_fetch", fetch),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            data = (await client.post("/internal/movies/ratings", headers=BOT, json={"imdbIds": ["tt0000010", "tt0000011"]})).json()["data"]
    # Ten days is stale for this year's film, not for a 1999 one; bookkeeping fields stay internal.
    assert [call.args[1] for call in fetch.await_args_list] == ["tt0000011"]
    assert data["tt0000010"] == scores
    await engine.dispose()


def test_only_quota_and_key_errors_stop_omdb():
    assert omdb.is_refusal("Request limit reached!") and omdb.is_refusal("Invalid API key!")
    assert not omdb.is_refusal("Error getting data.") and not omdb.is_refusal("Movie not found!")
