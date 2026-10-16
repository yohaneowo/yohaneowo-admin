"""The website's director card: directed films newest first, with cached TMDB data and OMDb scores."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.plugin.module_bot.movie import people

CREDITS = {
    "crew": [
        {"id": 1, "job": "Director", "title": "Old", "release_date": "1999-01-01", "vote_average": 7.84, "vote_count": 500},
        {"id": 2, "job": "Director", "title": "New", "release_date": "2024-05-01", "vote_average": 8, "vote_count": 3},
        {"id": 3, "job": "Writer", "title": "Not directed", "release_date": "2020-01-01"},
        {"id": 4, "job": "Director", "title": "Someday", "release_date": ""},
        {"id": 1, "job": "Director", "title": "Old again"},
    ]
}


def test_directed_films_newest_first_and_tmdb_score_needs_votes():
    films = people.directed_films(CREDITS)
    assert [(f["id"], f["year"], f["tmdbRating"]) for f in films] == [(2, 2024, None), (1, 1999, 7.8), (4, None, None)]
    assert people.directed_films({"cast": []}) is None


def test_lookup_builds_scores_and_caches_the_card():
    asyncio.run(check_lookup())


async def check_lookup():
    answers = {
        "/person/9/movie_credits": (200, CREDITS),
        "/movie/1/external_ids": (200, {"imdb_id": "tt0000001"}),
        "/movie/2/external_ids": (200, {"imdb_id": None}),
        "/movie/4/external_ids": (200, {"imdb_id": "tt0000004"}),
    }
    calls: list[str] = []

    async def tmdb_get(path, params=None):
        calls.append(path)
        if path == "/person/9":
            # No Chinese biography: falls back to English.
            if (params or {}).get("language") == "en-US":
                return 200, {"name": "Director", "biography": "English bio"}
            return 200, {"name": "导演", "biography": "", "profile_path": "/p.jpg"}
        return answers[path]

    store: dict = {}

    async def read_cache(namespace, keys):
        return {k: store[(namespace, k)] for k in keys if (namespace, k) in store}

    async def write_cache(namespace, items):
        store.update({(namespace, k): v for k, v in items.items()})

    ratings = AsyncMock(return_value={"tt0000001": {"imdbRating": 8.1, "rottenTomatoes": 90, "metacritic": 70}})

    class Session:
        # async with async_db_session() as db, db.begin(): — nothing to commit here.
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def begin(self):
            return self

    with (
        patch.object(people, "tmdb_get", tmdb_get),
        patch.object(people, "read_cache", read_cache),
        patch.object(people, "write_cache", write_cache),
        patch.object(people.omdb, "ratings", ratings),
        patch.object(people, "async_db_session", Session),
    ):
        card = await people.lookup(9, None)
        assert (card["name"], card["biography"], card["photo"]) == ("导演", "English bio", "https://image.tmdb.org/t/p/w342/p.jpg")
        assert [(f["id"], f["imdbRating"], f["rottenTomatoes"], f["metacritic"]) for f in card["films"]] == [(2, None, None, None), (1, 8.1, 90, 70), (4, None, None, None)]
        # Newest first goes to OMDb; films without an IMDb ID are skipped.
        assert ratings.await_args.args[1] == ["tt0000001", "tt0000004"]
        # The second look reads the cached card: no TMDB calls.
        before = len(calls)
        await people.lookup(9, None)
        assert len(calls) == before
        # The English card comes from the same cache entry.
        english = await people.lookup(9, None, "en")
        assert (english["biography"], [f["title"] for f in english["films"]], len(calls)) == ("English bio", ["New", "Old", "Someday"], before)
