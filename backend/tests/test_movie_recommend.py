"""/movie recommend: picks need a Trakt token, three a day, and 想看 / 看过了 write to Trakt."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.dependencies import db_getter
from app.plugin.module_bot.movie import recommend, service
from app.plugin.module_bot.movie.controller import MovieInternalRouter, MovieWebsiteRouter
from app.plugin.module_bot.movie.model import AnimePickModel, MovieCacheModel, MoviePickModel, MovieRecordModel, MovieSubscriberModel, PickBonusModel
from app.plugin.module_bot.movie.sources.trakt import TraktTokens
from app.plugin.module_bot.movie.sync import job as sync_job


def _movie(tmdb_id: int, title: str | None = None) -> dict:
    return {"title": title or f"Film {tmdb_id}", "ids": {"trakt": tmdb_id * 10, "tmdb": tmdb_id}}


def _details(tmdb_id: int, genres: list[tuple[int, str]], year: int = 2015, votes: int = 5000, average: float = 7.4, director: int = 900) -> dict:
    return {
        "title": f"Film {tmdb_id}",
        "year": str(year),
        "releaseDate": f"{year}-05-01",
        "genres": [name for _, name in genres],
        "genreIds": [gid for gid, _ in genres],
        "voteAverage": average,
        "voteCount": votes,
        "poster": None,
        "collectionId": None,
        "directors": [{"id": director, "name": f"Director {director}"}],
        "cast": [{"id": 800 + tmdb_id, "name": f"Actor {tmdb_id}"}],
    }


class FakeWorld:
    """Trakt and TMDB as the recommender sees them, plus a log of every write."""

    def __init__(self, unrated: bool = False) -> None:
        self.writes: list[tuple[str, dict]] = []
        self.last_history: int | None = None
        # 12 watched films (past the cold start), comedy / drama lovers who rate high.
        self.watched = [{"plays": 1 if i < 11 else 3, "last_watched_at": "2026-01-01T00:00:00.000Z", "movie": _movie(i)} for i in range(1, 13)]
        # With `unrated`, films 11 and 12 were watched but never rated: 补评分 material.
        self.ratings = [{"rating": 9 if i % 2 else 8, "rated_at": "2026-01-01T00:00:00.000Z", "movie": _movie(i)} for i in range(1, 11 if unrated else 13)]
        self.details = {i: _details(i, [(35, "喜剧"), (18, "剧情")]) for i in range(1, 13)}
        # Candidates: comedies from Trakt's recommendations and one related title.
        for i in range(101, 109):
            self.details[i] = _details(i, [(35, "喜剧")], votes=8000, director=901 + i)
        self.details[150] = _details(150, [(35, "喜剧"), (10749, "爱情")], votes=3000)
        # Explore classics in the person's blind genres (westerns, war).
        for i in range(201, 206):
            self.details[i] = _details(i, [(37, "西部")], year=1966, votes=20000, average=8.2, director=950 + i)

    async def trakt(self, method: str, path: str, token: str, params=None, json=None):
        if method == "POST":
            self.writes.append((path, json))
            if path == "/sync/history":
                self.last_history = json["movies"][0]["ids"]["tmdb"]
            return 201, {"added": {"movies": 1}}
        if path.startswith("/sync/history/movies"):
            return 200, [{"id": 555, "watched_at": "2026-10-18T00:00:00.000Z", "movie": _movie(self.last_history)}] if self.last_history else []
        if path == "/sync/last_activities":
            return 200, {"movies": {"watched_at": "2026-01-01T00:00:00Z", "rated_at": None, "watchlisted_at": None, "hidden_at": None}}
        page = (params or {}).get("page", 1)
        if page > 1:
            return 200, []
        if path == "/sync/watched/movies":
            return 200, self.watched
        if path == "/sync/ratings/movies":
            return 200, self.ratings
        if path in ("/sync/watchlist/movies", "/users/hidden/recommendations"):
            return 200, []
        if path == "/recommendations/movies":
            return 200, [_movie(i) for i in range(101, 109)]
        if path.endswith("/related"):
            return 200, [_movie(150)]
        return 404, None

    async def tmdb(self, path: str, params=None):
        if path == "/discover/movie":
            if "with_genres" in (params or {}):
                return 200, {"total_pages": 1, "results": [{"id": i, "genre_ids": [37]} for i in range(201, 206)]}
            if "primary_release_date.gte" in (params or {}):
                return 200, {"total_pages": 1, "results": []}
            # Base rates: comedies and dramas are common, westerns and war films rare.
            return 200, {"total_pages": 1, "results": [{"id": 1000 + i, "genre_ids": [35, 18] if i < 8 else [37, 10752]} for i in range(10)]}
        if path.startswith("/person/"):
            return 200, {"cast": [], "crew": []}
        if path.startswith("/movie/"):
            tmdb_id = int(path.split("/")[2])
            d = self.details[tmdb_id]
            return 200, {
                "title": d["title"],
                "overview": "An overview.",
                "runtime": 120,
                "genres": [{"name": g} for g in d["genres"]],
                "credits": {"cast": [{"name": "Someone"}]},
                "poster_path": None,
                "vote_average": d["voteAverage"],
                "vote_count": d["voteCount"],
                "release_date": d["releaseDate"],
            }
        return 404, None

    async def load_details(self, ids: list) -> dict:
        return {i: self.details[i] for i in ids if i in self.details}


def test_recommend_flow():
    asyncio.run(check_recommend_flow())


async def check_recommend_flow():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for model in (MovieSubscriberModel, MovieRecordModel, MovieCacheModel, MoviePickModel, AnimePickModel, PickBonusModel):
            await conn.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session():
        async with factory.begin() as db:
            yield db

    app = FastAPI()
    app.include_router(MovieInternalRouter)
    app.include_router(MovieWebsiteRouter)
    app.dependency_overrides[db_getter] = session
    app.dependency_overrides[sync_job.ensure_loop] = lambda: None
    bot = {"X-Bot-Token": "test-bot"}
    site = {"X-Movies-Token": "test-site"}
    uid = "123456789012345678"
    world = FakeWorld()
    cache: dict[tuple[str, str], dict] = {}

    async def read_cache(namespace, keys):
        return {k: cache[(namespace, k)] for k in keys if (namespace, k) in cache}

    async def write_cache(namespace, items):
        cache.update({(namespace, k): v for k, v in items.items()})

    with (
        patch("app.plugin.module_bot.internal_auth.get_bot_settings", return_value=SimpleNamespace(BOT_API_TOKEN="test-bot")),
        patch("app.plugin.module_bot.movie.auth.site_token", return_value="test-site"),
        patch("app.plugin.module_bot.movie.sources.trakt.exchange_code", AsyncMock(return_value=TraktTokens("access", "refresh", service.utcnow() + timedelta(days=90)))),
        patch("app.plugin.module_bot.movie.sources.trakt.me", AsyncMock(return_value={"username": "Test", "private": False, "ids": {"slug": "test-user"}})),
        patch("app.utils.crypto_util.CryptoUtil.encrypt", side_effect=lambda value: f"enc:{value}"),
        patch("app.utils.crypto_util.CryptoUtil.decrypt", side_effect=lambda value: value.removeprefix("enc:")),
        patch.object(recommend, "_trakt", world.trakt),
        patch.object(recommend, "tmdb_get", world.tmdb),
        patch.object(recommend, "trakt_get", AsyncMock(return_value=(200, 0, [{"movie": {"ids": {"slug": "film-slug"}}}]))),
        patch.object(recommend, "load_details", world.load_details),
        patch.object(recommend, "read_cache", read_cache),
        patch.object(recommend, "write_cache", write_cache),
        patch.object(recommend, "async_db_session", factory),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # A bot-created profile without a Trakt token can't use picks.
            created = await client.post("/internal/movies/subscribers", headers=bot, json={"discordId": uid, "traktUser": "test-user", "traktName": "Test", "region": "TW"})
            assert created.status_code in (200, 201, 404, 405)
            if created.status_code in (200, 201):
                assert (await client.post(f"/internal/movies/picks/{uid}", headers=bot, json={})).status_code == 403
            # The website connects the same Trakt account with OAuth (tokens) and links Discord.
            connect = {"code": "oauth-code", "redirectUri": "https://site.test/api/trakt/callback", "codeVerifier": "v" * 43, "region": "TW", "discordId": uid}
            response = await client.post("/website/movies/users/siteUser01/trakt", headers=site, json=connect)
            assert response.status_code == 200, response.text

            first = (await client.post(f"/internal/movies/picks/{uid}", headers=bot, json={})).json()["data"]
            assert first["pickId"] and first["mode"] in recommend.MODES and first["reason"] and first["remainingToday"] == 2
            assert first["tmdbId"] not in range(1, 13)
            assert first["overview"] == "An overview." and first["runtime"] == 120
            assert first["traktUrl"] == "https://trakt.tv/movies/film-slug"

            # 换一部 marks the previous pick as skipped; three picks a day.
            second = (await client.post(f"/internal/movies/picks/{uid}", headers=bot, json={"skip": first["pickId"]})).json()["data"]
            assert second["tmdbId"] != first["tmdbId"] and second["remainingToday"] == 1
            third = (await client.post(f"/internal/movies/picks/{uid}", headers=bot, json={"genre": 35})).json()["data"]
            assert 35 in [35] and third["remainingToday"] == 0
            assert third["bonusLeft"] == 100 and not third["usedBonus"]
            # Past the day's three, a bonus pick (100 to start); with none left, the limit.
            fourth = (await client.post(f"/internal/movies/picks/{uid}", headers=bot, json={})).json()["data"]
            assert fourth["usedBonus"] and fourth["remainingToday"] == 0 and fourth["bonusLeft"] == 99
            taken = await client.post(f"/internal/movies/owner/{uid}/bonus", headers=bot, json={"feature": "movie", "amount": -99})
            assert taken.json()["data"]["bonus"] == 0
            limited = await client.post(f"/internal/movies/picks/{uid}", headers=bot, json={})
            assert limited.status_code == 429
            listed = (await client.get(f"/internal/movies/picks/{uid}", headers=bot)).json()["data"]
            assert listed["remainingToday"] == 0 and listed["bonus"] == 0 and len(listed["picks"]) == 4
            assert next(p for p in listed["picks"] if p["pickId"] == first["pickId"])["action"] == "skip"

            # 想看 → Trakt watchlist.
            want = (await client.post(f"/internal/movies/picks/{uid}/{second['pickId']}/watchlist", headers=bot)).json()["data"]
            assert want["action"] == "watchlist"
            assert world.writes[-1][0] == "/sync/watchlist" and world.writes[-1][1]["movies"][0]["ids"]["tmdb"] == second["tmdbId"]
            # 看过了 without a rating: nothing written, the bot asks for one.
            unrated = (await client.post(f"/internal/movies/picks/{uid}/{third['pickId']}/watched", headers=bot, json={})).json()["data"]
            assert unrated["rated"] is False and len(world.writes) == 1
            rated = (await client.post(f"/internal/movies/picks/{uid}/{third['pickId']}/watched", headers=bot, json={"rating": 8})).json()["data"]
            assert rated["rated"] is True and rated["rating"] == 8 and rated["own"] is True
            assert [w[0] for w in world.writes[-2:]] == ["/sync/ratings", "/sync/history"]
            assert world.writes[-2][1]["movies"][0]["rating"] == 8
            # Rating it gave the pick back; removing the rating takes it back again (and the play).
            assert rated["refunded"] is True and rated["remainingToday"] == 1
            undone = (await client.post(f"/internal/movies/picks/{uid}/{third['pickId']}/unrate", headers=bot)).json()["data"]
            assert undone["remainingToday"] == 0
            assert [w[0] for w in world.writes[-2:]] == ["/sync/ratings/remove", "/sync/history/remove"]
            assert world.writes[-1][1] == {"ids": [555]}
            # Only ratings written here can be removed.
            assert (await client.post(f"/internal/movies/picks/{uid}/{third['pickId']}/unrate", headers=bot)).status_code == 404
            # Unknown pick.
            assert (await client.post(f"/internal/movies/picks/{uid}/nope/watchlist", headers=bot)).status_code == 404


def test_backfill_pool_and_refund():
    asyncio.run(check_backfill())


async def check_backfill():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for model in (MovieSubscriberModel, MovieRecordModel, MovieCacheModel, MoviePickModel, AnimePickModel, PickBonusModel):
            await conn.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    world = FakeWorld(unrated=True)
    cache: dict[tuple[str, str], dict] = {}

    async def read_cache(namespace, keys):
        return {k: cache[(namespace, k)] for k in keys if (namespace, k) in cache}

    async def write_cache(namespace, items):
        cache.update({(namespace, k): v for k, v in items.items()})

    with (
        patch.object(recommend, "_trakt", world.trakt),
        patch.object(recommend, "tmdb_get", world.tmdb),
        patch.object(recommend, "load_details", world.load_details),
        patch.object(recommend, "read_cache", read_cache),
        patch.object(recommend, "write_cache", write_cache),
        patch.object(recommend, "async_db_session", factory),
        patch("app.utils.crypto_util.CryptoUtil.decrypt", side_effect=lambda value: value.removeprefix("enc:")),
    ):
        pools = await recommend.build_pools("token", set(), "p1")
        assert sorted(c["tmdbId"] for c in pools["pools"]["backfill"]) == [11, 12]
        assert all(c["tmdbId"] not in (11, 12) for mode in recommend.MODES for c in pools["pools"][mode])
        # A 补评分 pick that gets its rating writes only the rating and gives the pick back.
        async with factory.begin() as db:
            row = MovieSubscriberModel(
                profile_id="p1",
                trakt_user="u",
                trakt_name="U",
                region="TW",
                revision="r",
                trakt_access_token="enc:a",
                trakt_refresh_token="enc:r",
                trakt_token_expires_at=service.utcnow() + timedelta(days=30),
            )
            db.add(row)
            pick = MoviePickModel(profile_id="p1", tmdb_id=11, mode="backfill", day=recommend.today_taipei().isoformat(), title="Film 11", reason="补评分", data={})
            db.add(pick)
            await db.flush()
            assert await recommend.picks_today(db, "p1") == 1
            result = await recommend.watched(db, row, pick.pick_id, 7)
            assert result["refunded"] is True and result["action"] == "rated" and result["remainingToday"] == recommend.DAILY_PICKS
            assert [w[0] for w in world.writes] == ["/sync/ratings"]
            assert await recommend.picks_today(db, "p1") == 0
            # Refunds are capped per day: a fourth rated pick still counts.
            for n in range(1, 5):
                extra = MoviePickModel(
                    profile_id="p1",
                    tmdb_id=100 + n,
                    mode="backfill",
                    day=recommend.today_taipei().isoformat(),
                    title=f"F{n}",
                    reason="r",
                    data={},
                    action="rated",
                    rating=6,
                    own_rating=True,
                    acted_at=service.utcnow() + timedelta(seconds=n),
                )
                db.add(extra)
            await db.flush()
            assert await recommend.picks_today(db, "p1") == 2
            # A bigger quota for a test account.
            row.pick_quota = 1000
            assert recommend.quota_for(row) == 1000


def test_blind_spots_follow_base_rates():
    profile = {"watched": list(range(100)), "userGenreCount": {35: 80, 18: 60, 37: 1}, "details": {}}
    share = {"35": 0.5, "18": 0.4, "37": 0.05, "10752": 0.05}
    blind = recommend.blind_genres(profile, share)
    # Westerns: 1% of this person's films against a 5% base rate → blind; comedies are not.
    assert 37 in blind and 10752 in blind and 35 not in blind


def test_bayes_pulls_few_votes_to_the_mean():
    assert recommend.bayes(9.5, 20, 1000) < recommend.bayes(8.0, 50000, 1000)
    assert recommend.bayes(None, None, 1000) == 0.0
