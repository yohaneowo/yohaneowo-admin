"""Anime series: Bangumi / AniList / MyAnimeList data on the anime section."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.plugin.module_bot.movie.schema import ShowSection
from app.plugin.module_bot.movie.sources.base import SourceUnavailable
from app.plugin.module_bot.movie.sync import anime


def test_slim_entries_keep_the_wanted_roles():
    media = {
        "id": 269,
        "idMal": 269,
        "averageScore": 79,
        "title": {"romaji": "BLEACH", "english": "Bleach", "native": "BLEACH"},
        "studios": {"nodes": [{"id": 1, "name": "Studio Pierrot"}]},
        "staff": {
            "edges": [
                {"role": "Director", "node": {"id": 10, "name": {"full": "Noriyuki Abe", "native": "阿部記之"}}},
                {"role": "Storyboard (ep 3)", "node": {"id": 11, "name": {"full": "Someone", "native": None}}},
                {"role": "Chief Animation Director (eps 1-20)", "node": {"id": 12, "name": {"full": "Ani Mator", "native": "作画"}}},
            ]
        },
        "relations": {"edges": [{"relationType": "SEQUEL", "node": {"id": 99, "type": "ANIME", "format": "TV", "startDate": {"year": 2022, "month": 10, "day": 11}}}]},
    }
    slim = anime.slim_anilist(media)
    assert (slim["score"], slim["titleEn"], slim["sequels"]) == (7.9, "Bleach", [{"id": 99, "start": "2022-10-11"}])
    assert [(p["role"], p["en"]) for p in slim["staff"]] == [("director", "Noriyuki Abe"), ("chiefAnimationDirector", "Ani Mator"), ("studio", "Studio Pierrot")]

    subject = {"id": 1600, "name": "BLEACH", "name_cn": "死神", "rating": {"score": 7.3, "total": 5632}}
    persons = [{"id": 1, "name": "阿部記之", "relation": "导演"}, {"id": 2, "name": "ぴえろ", "relation": "动画制作"}, {"id": 3, "name": "X", "relation": "原画"}]
    slim = anime.slim_bangumi(subject, persons)
    assert (slim["nameCn"], slim["score"], slim["votes"]) == ("死神", 7.3, 5632)
    assert [(p["role"], p["zh"]) for p in slim["staff"]] == [("director", "阿部記之"), ("studio", "ぴえろ")]


def test_people_merge_anilist_and_bangumi():
    entry = {
        "anilist": {
            "staff": [
                {"role": "director", "id": 10, "en": "Noriyuki Abe", "native": "阿部記之"},
                {"role": "studio", "id": 1, "en": "Studio Pierrot", "native": "Studio Pierrot"},
            ]
        },
        "bangumi": {
            "staff": [
                {"role": "director", "id": 1, "zh": "阿部 記之"},
                {"role": "studio", "id": 2, "zh": "ぴえろ"},
                {"role": "chiefAnimationDirector", "id": 3, "zh": "作画 太郎"},
            ]
        },
    }
    # The second season repeats the director: listed once.
    people = anime._people([entry, entry])
    assert [(p["role"], p["id"], p["name"], p["nameEn"]) for p in people] == [
        ("director", 10, "阿部 記之", "Noriyuki Abe"),
        ("studio", 1, "ぴえろ", "Studio Pierrot"),
        ("chiefAnimationDirector", -3, "作画 太郎", None),
    ]


def _section():
    def item(show_id, title, rating, seasons):
        return {
            "id": show_id,
            "title": title,
            "originalTitle": title,
            "myRating": rating,
            "watchedAt": "2026-01-01",
            "seasons": [{"number": n, "airDate": d, "myRating": None} for n, d in seasons],
        }

    return {
        "watchedCount": 3,
        "episodes": 3,
        "ratingCount": 2,
        "mean": 7.0,
        "distribution": [0] * 10,
        "genres": [],
        "people": [],
        "items": [item(1, "A", 9, [(1, "2020-01-01"), (2, "2022-01-01")]), item(2, "B", 8, [(1, "2021-01-01")]), item(3, "C", None, [(1, None)])],
    }


def _entry(anilist_id, mal_id, bangumi_id, score):
    return {
        "cacheVersion": anime.CACHE_VERSION,
        "fetchedAt": "2099-01-01T00:00:00+00:00",
        "airDate": "2020-01-01",
        "anilist": {"id": anilist_id, "idMal": mal_id, "score": score, "titleEn": f"EN {anilist_id}", "staff": [{"role": "director", "id": 7, "en": "Dee Rector", "native": "監督"}]},
        "bangumi": {"id": bangumi_id, "nameCn": f"中 {bangumi_id}", "score": score - 1, "votes": 100, "staff": []},
    }


def test_enrich_fills_scores_staff_and_favourites():
    store = {}

    async def read_cache(namespace, keys):
        return {k: store[(namespace, k)] for k in keys if (namespace, k) in store}

    async def write_cache(namespace, items):
        store.update({(namespace, k): v for k, v in items.items()})

    entries = {(1, 1): _entry(101, 201, 301, 8.0), (1, 2): _entry(102, 202, 302, 9.0), (2, 1): _entry(103, 203, 303, 7.0)}
    match = AsyncMock(side_effect=lambda item, season, previous: entries[(item["id"], season["number"])])
    mal_score = AsyncMock(side_effect=lambda db, mal_id: {"score": mal_id / 25, "votes": 5})

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def begin(self):
            return self

    section = _section()
    with (
        patch.object(anime, "read_cache", read_cache),
        patch.object(anime, "write_cache", write_cache),
        patch.object(anime, "match_season", match),
        patch.object(anime.mal, "score", mal_score),
        patch.object(anime, "async_db_session", Session),
    ):
        assert asyncio.run(anime.enrich_anime(section)) is False
        # The second season gets the first one's match to follow AniList's sequels.
        assert match.await_args_list[1].args[2] == entries[(1, 1)]
        first = section["items"][0]
        assert (first["bangumiScore"], first["malScore"], first["malVotes"], first["titleEn"]) == (7.0, 8.04, 5, "EN 101")
        assert [(s["bangumiScore"], s["malScore"], s["titleZh"]) for s in first["seasons"]] == [(7.0, 8.04, "中 301"), (8.0, 8.08, "中 302")]
        assert [(p["role"], p["name"], p["nameEn"]) for p in first["staff"]] == [("director", "監督", "Dee Rector")]
        # Both rated series share the director (average 8.5 ≥ mean 7.0 + 0.3).
        assert [(p["role"], p["count"]) for p in section["people"]] == [("director", 2)]
        ShowSection.model_validate(section)

        # Cached now: a second run looks nothing up.
        match.reset_mock()
        asyncio.run(anime.enrich_anime(_section()))
        assert match.await_count == 0


def test_enrich_reports_leftovers_but_not_outages():
    async def read_cache(namespace, keys):
        return {}

    async def write_cache(namespace, items):
        pass

    with (
        patch.object(anime, "read_cache", read_cache),
        patch.object(anime, "write_cache", write_cache),
        patch.object(anime, "match_season", AsyncMock(return_value=_entry(1, None, 2, 7.0))),
    ):
        # Out of time: more to do at the next sync.
        assert asyncio.run(anime.enrich_anime(_section(), budget_seconds=-1)) is True
    with (
        patch.object(anime, "read_cache", read_cache),
        patch.object(anime, "write_cache", write_cache),
        patch.object(anime, "match_season", AsyncMock(side_effect=SourceUnavailable("down"))),
    ):
        # A source that is down waits for the regular schedule instead of resyncing at once.
        assert asyncio.run(anime.enrich_anime(_section())) is False


def test_mal_scores_skip_one_failure_but_stop_when_down():
    store = {}

    async def read_cache(namespace, keys):
        return {}

    async def write_cache(namespace, items):
        store.update(items)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def begin(self):
            return self

    flaky = AsyncMock(side_effect=[SourceUnavailable("slow"), {"score": 8.0, "votes": 3}, {"score": 7.0, "votes": 2}])
    with (
        patch.object(anime, "read_cache", read_cache),
        patch.object(anime, "write_cache", write_cache),
        patch.object(anime, "async_db_session", Session),
        patch.object(anime.mal, "score", flaky),
    ):
        # One slow answer skips that ID only.
        scores, left = asyncio.run(anime._mal_scores([1, 2, 3], float("inf")))
        assert (sorted(store), left, scores["2"]["score"]) == (["2", "3"], False, 8.0)
        # A source that keeps failing is given up on.
        down = AsyncMock(side_effect=SourceUnavailable("down"))
        with patch.object(anime.mal, "score", down):
            asyncio.run(anime._mal_scores([4, 5, 6, 7], float("inf")))
        assert down.await_count == anime.MAL_MAX_FAILURES
