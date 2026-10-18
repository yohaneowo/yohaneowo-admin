"""/anime recommend: taste from the snapshot, candidates, filters, the airing switch, where a pick
lands on Trakt, and a whole pool build with fake sources."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.plugin.module_bot.movie import anime_recommend as ar


def _item(show_id, title, rating, genres, seasons, watched_eps, tags=(), staff=(), year=2020):
    return {
        "id": show_id,
        "title": title,
        "myRating": rating,
        "genres": list(genres),
        "tags": list(tags),
        "staff": list(staff),
        "year": year,
        "episodesWatched": watched_eps,
        "seasons": [{"number": n, "episodes": eps, "anilistId": aid, "myRating": None} for n, eps, aid in seasons],
    }


SNAPSHOT = {
    "anime": {
        "mean": 7.0,
        "items": [
            # Liked, first season watched (12 of 24 episodes): its second season is a sequel seed.
            _item(1, "进击的巨人", 9, ["Action", "Drama"], [(1, 12, 101), (2, 12, 102)], 12, tags=["Military"], staff=[{"role": "studio", "id": 7, "name": "WIT"}]),
            _item(2, "间谍过家家", 8, ["Action", "Comedy"], [(1, 12, 201)], 12, staff=[{"role": "studio", "id": 7, "name": "WIT"}]),
            # Disliked: every season of it is ruled out.
            _item(3, "烂片", 4, ["Romance"], [(1, 12, 301), (2, 12, 302)], 12),
            # Unrated, nothing watched.
            _item(4, "没看", None, ["Comedy"], [(1, 12, 401)], 0),
        ],
    }
}


def test_profile_from_the_snapshot():
    profile = ar.build_profile(SNAPSHOT)
    assert profile["watched"] == {101, 201, 301}
    assert profile["disliked"] == {301, 302}
    assert [s["anilistId"] for s in profile["sequelSeeds"]] == [101, 201]
    assert [s["anilistId"] for s in profile["liked"]] == [101, 201]
    # A favourite studio needs two works and a positive weight.
    assert [s["name"] for s in profile["studios"]] == ["WIT"]
    # Never-watched genres, the common ones first; Action is watched.
    assert profile["blindGenres"] == ["Fantasy", "Adventure", "Supernatural"]
    assert profile["taste"]["g:Action"] > 0 > profile["taste"]["g:Romance"]


def _media(anilist_id, **extra):
    return {
        "id": anilist_id,
        "idMal": anilist_id + 1000,
        "type": "ANIME",
        "format": "TV",
        "status": "FINISHED",
        "episodes": 12,
        "isAdult": False,
        "averageScore": 85,
        "popularity": 200000,
        "startDate": {"year": 2021},
        "genres": ["Action"],
        "tags": [{"name": "Military", "rank": 80}, {"name": "Twist", "rank": 90, "isMediaSpoiler": True}, {"name": "Minor", "rank": 20}],
        "studios": {"nodes": [{"id": 7, "name": "WIT"}]},
        "title": {"native": f"作品{anilist_id}", "romaji": f"Work {anilist_id}", "english": None},
        "coverImage": {"extraLarge": "https://img/x.jpg"},
        "relations": {"edges": []},
        **extra,
    }


def test_candidates_leave_out_what_cannot_be_recommended():
    c = ar.candidate(_media(5, relations={"edges": [{"relationType": "PREQUEL", "node": {"id": 4, "type": "ANIME"}}]}))
    assert (c["score"], c["tags"], c["prequels"], c["malId"]) == (8.5, ["Military"], [4], 1005)
    assert ar.candidate(_media(6, format="MUSIC")) is None
    assert ar.candidate(_media(7, status="NOT_YET_RELEASED")) is None
    assert ar.candidate(_media(8, isAdult=True)) is None
    assert ar.candidate({**_media(9), "type": "MANGA"}) is None


def test_later_seasons_only_follow_a_watched_season_outside_the_sequel_pool():
    profile = ar.build_profile(SNAPSHOT)
    later = ar.candidate(_media(102, relations={"edges": [{"relationType": "PREQUEL", "node": {"id": 101, "type": "ANIME"}}]}))
    stranger = ar.candidate(_media(902, relations={"edges": [{"relationType": "PREQUEL", "node": {"id": 901, "type": "ANIME"}}]}))
    assert ar._fits(later, profile) and not ar._fits(stranger, profile)
    assert ar._fits(stranger, profile, sequel=True)
    assert not ar._fits(ar.candidate(_media(301)), profile)


def test_filters():
    c = {"genres": ["Action"], "year": 2015, "format": "TV_SHORT", "episodes": 24}
    assert ar.matches(c, {"genre": "Action", "decade": 2010, "format": "TV", "length": "cour2"})
    assert not ar.matches(c, {"genre": "Drama"})
    assert not ar.matches(c, {"decade": 2000})
    assert not ar.matches(c, {"format": "MOVIE"})
    assert not ar.matches(c, {"length": "short"}) and not ar.matches(c, {"length": "long"})
    # An unknown episode count can't satisfy a length filter.
    assert not ar.matches({**c, "episodes": None}, {"length": "short"})


def test_the_airing_switch_and_the_airing_filter():
    on, off = SimpleNamespace(anime_airing=True), SimpleNamespace(anime_airing=False)
    assert "airing" in ar.modes_for(on, {}) and "airing" not in ar.modes_for(off, {})
    # Asking for airing only wins over the switch.
    assert ar.modes_for(off, {"airing": True}) == ("airing",)


SHOW = {"traktId": 1420, "slug": "attack-on-titan", "seasons": {"3": 22, "1": 25}}


def test_where_a_season_lands_on_trakt():
    whole = ar.season_target({"type": "tv", "tmdbId": 1429, "season": 1, "offset": 0}, SHOW, 25)
    assert (whole["episodes"], whole["watchable"], whole["url"]) == (None, True, "https://trakt.tv/shows/attack-on-titan/seasons/1")
    # A split season's second part: its episodes within the bigger Trakt season.
    part2 = ar.season_target({"type": "tv", "tmdbId": 1429, "season": 3, "offset": 12}, SHOW, 10)
    assert part2["episodes"] == [13, 22]
    # Counts that can't fit, an unknown season, or a mapping without one: links only.
    assert ar.season_target({"type": "tv", "tmdbId": 1429, "season": 3, "offset": 12}, SHOW, 12) is None
    assert ar.season_target({"type": "tv", "tmdbId": 1429, "season": 9, "offset": 0}, SHOW, 12) is None
    assert ar.season_target({"type": "tv", "tmdbId": 1429, "season": None, "offset": 0}, SHOW, 500) is None
    # Still airing (episode count unknown): the watchlist only.
    airing = ar.season_target({"type": "tv", "tmdbId": 1429, "season": 3, "offset": 0}, SHOW, None)
    assert airing["watchable"] is False


def test_trakt_bodies():
    season = {"type": "show", "traktId": 1420, "season": 3, "episodes": None}
    part = {**season, "episodes": [13, 15]}
    film = {"type": "movie", "traktId": 99}
    assert ar.show_payload(season, watched_at="T") == {"shows": [{"ids": {"trakt": 1420}, "seasons": [{"number": 3, "watched_at": "T"}]}]}
    assert ar.show_payload(part, watched_at="T")["shows"][0]["seasons"][0]["episodes"] == [{"number": n, "watched_at": "T"} for n in (13, 14, 15)]
    # Ratings go to the whole season, even for a part of it.
    assert ar.show_payload(part, rating=9, rated_at="T") == {"shows": [{"ids": {"trakt": 1420}, "seasons": [{"number": 3, "rating": 9, "rated_at": "T"}]}]}
    assert ar.show_payload(film, watched_at="T", rating=8, rated_at="T") == {"movies": [{"ids": {"trakt": 99}, "watched_at": "T", "rating": 8, "rated_at": "T"}]}


def test_mode_weights_follow_wants_and_skips():
    picks = [SimpleNamespace(mode="sequel", action="watchlist"), SimpleNamespace(mode="explore", action="skip"), SimpleNamespace(mode="explore", action="skip")]
    weights = ar.mode_weights(picks, ar.MODES)
    assert abs(sum(weights.values()) - 1) < 1e-9
    assert weights["sequel"] > ar.CONFIG["initialWeights"]["sequel"] and weights["explore"] < ar.CONFIG["initialWeights"]["explore"]


def test_a_whole_pool_build_with_fake_sources():
    sequel = _media(102, relations={"edges": [{"relationType": "PREQUEL", "node": {"id": 101, "type": "ANIME"}}]})
    recommended = _media(500, genres=["Action", "Drama"])
    hidden = _media(501)
    airing_show = _media(600, status="RELEASING", episodes=None, averageScore=None)

    async def query(text, variables):
        if text == ar.SEQUEL_QUERY:
            return {"Page": {"media": [{"id": 101, "relations": {"edges": [{"relationType": "SEQUEL", "node": sequel}]}}]}}
        if text == ar.RECOMMENDATION_QUERY:
            return {"Page": {"media": [{"id": 101, "recommendations": {"nodes": [{"rating": 90, "mediaRecommendation": recommended}, {"rating": 50, "mediaRecommendation": hidden}]}}]}}
        if text == ar.STUDIO_QUERY:
            return {"Studio": {"media": {"nodes": [_media(700, genres=["Fantasy"])]}}}
        if text == ar.AIRING_QUERY:
            return {"Page": {"media": [airing_show] if variables["page"] == 1 else []}}
        return {"Page": {"media": []}}

    store = {}

    async def read_cache(namespace, keys):
        return {k: store[(namespace, k)] for k in keys if (namespace, k) in store}

    async def write_cache(namespace, items):
        store.update({(namespace, k): v for k, v in items.items()})

    lists = {"watchlistShows": [5000], "watchlistMovies": [], "hiddenShows": [5010]}
    mapping = {500: {"type": "tv", "tmdbId": 5000, "season": 1, "offset": 0}, 501: {"type": "tv", "tmdbId": 5010, "season": 1, "offset": 0}}
    row = SimpleNamespace(profile_id="p", snapshot=SNAPSHOT, synced_at=None)
    with (
        patch.object(ar, "_query", query),
        patch.object(ar, "read_cache", read_cache),
        patch.object(ar, "write_cache", write_cache),
        patch.object(ar, "trakt_lists", AsyncMock(return_value=lists)),
        patch.object(ar, "last_activities", AsyncMock(return_value={})),
        patch.object(ar.anime_ids, "lookup", AsyncMock(return_value=mapping)),
    ):
        pools = asyncio.run(ar.build_pools(row, "token"))["pools"]
    assert [c["anilistId"] for c in pools["sequel"]] == [102]
    comfort = {c["anilistId"]: c for c in pools["comfort"]}
    # On the watchlist: kept and lifted; hidden on Trakt: gone.
    assert comfort[500]["inWatchlist"] is True and 501 not in comfort
    # A favourite studio's work in a blind genre goes to bridge, not comfort.
    assert 700 not in comfort and [c["anilistId"] for c in pools["bridge"]] == [700]
    assert [c["anilistId"] for c in pools["airing"]] == [600]
