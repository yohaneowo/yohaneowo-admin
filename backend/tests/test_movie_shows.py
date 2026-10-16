"""The TV section of the statistics snapshot."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.plugin.module_bot.movie.schema import ShowSection
from app.plugin.module_bot.movie.sync import shows
from app.plugin.module_bot.movie.sync.history import watched_episodes


def test_watched_episodes_use_plays_without_seasons():
    assert watched_episodes({"plays": 30}) == 30
    assert watched_episodes({"plays": None}) == 0


def test_watched_episodes_skip_specials_and_unplayed():
    entry = {
        "seasons": [
            {"number": 0, "episodes": [{"number": 1, "plays": 1}]},
            {"number": 1, "episodes": [{"number": 1, "plays": 2}, {"number": 2, "plays": 0}]},
            {"number": 2, "episodes": [{"number": 1, "plays": 1}]},
        ]
    }
    assert watched_episodes(entry) == 2


def test_aired_episodes_count_regular_seasons_up_to_the_last_aired_one():
    details = {
        "seasons": [{"season_number": 0, "episode_count": 5}, {"season_number": 1, "episode_count": 10}, {"season_number": 2, "episode_count": 8}, {"season_number": 3, "episode_count": 10}],
        "last_episode_to_air": {"season_number": 3, "episode_number": 4},
    }
    assert shows.aired_episodes(details) == 22
    assert shows.aired_episodes({"seasons": []}) is None


def test_show_section_lists_progress_ratings_and_favourite_creators():
    def show(tmdb, title, imdb=None):
        return {"title": title, "ids": {"tmdb": tmdb, "imdb": imdb}}

    history = {
        "watchedShows": [
            # 30 plays with rewatches: capped at the 24 aired episodes.
            {"plays": 30, "lastWatchedAt": "2026-10-01T20:00:00.000Z", "show": show(1, "A", "tt0000101"), "episodes": 30},
            {"plays": 5, "lastWatchedAt": "2025-01-01T20:00:00.000Z", "show": show(2, "B"), "episodes": 5},
        ],
        # Rated but never marked watched: listed with no episodes, at its rating date.
        "showRatings": [
            {"rating": 8, "ratedAt": "2026-10-02T00:00:00.000Z", "show": show(1, "A")},
            {"rating": 8.5, "ratedAt": "2025-06-01T00:00:00.000Z", "show": show(2, "B")},
            {"rating": 6, "ratedAt": "2024-01-01T00:00:00.000Z", "show": show(3, "C")},
            {"rating": 7, "ratedAt": "2023-01-01T00:00:00.000Z", "show": show(4, "D")},
        ],
        "wholeShowRatings": [
            {"rating": 9, "ratedAt": "2026-10-02T00:00:00.000Z", "show": show(1, "A")},
            {"rating": 8.5, "ratedAt": "2025-06-01T00:00:00.000Z", "show": show(2, "B")},
            {"rating": 6, "ratedAt": "2024-01-01T00:00:00.000Z", "show": show(3, "C")},
        ],
        # Season ratings stay on their seasons; D has no whole-show rating, so no score of its own.
        "seasonRatings": [
            {"rating": 7, "ratedAt": "2026-10-02T00:00:00.000Z", "show": show(1, "A"), "season": 1},
            {"rating": 7, "ratedAt": "2023-01-01T00:00:00.000Z", "show": show(4, "D"), "season": 1},
        ],
    }
    creator = {"id": 50, "name": "Maker", "photo": None}
    details = {
        1: {
            "title": "甲",
            "year": "2024",
            "firstAirDate": "2024-01-05",
            "genres": ["剧情"],
            "genreIds": [18],
            "airedEpisodes": 24,
            "seasons": [
                {"number": 1, "name": "第 1 季", "episodes": 12, "airDate": "2024-01-05", "publicScore": 8.1},
                {"number": 2, "name": "第 2 季", "episodes": 12, "airDate": "2025-01-05", "publicScore": None},
            ],
            "creators": [creator],
            "cast": [],
            "voteAverage": 8.45,
            "voteCount": 900,
        },
        2: {"title": "乙", "year": "2020", "firstAirDate": "2020-03-01", "genres": ["剧情", "犯罪"], "genreIds": [18, 80], "airedEpisodes": 5, "creators": [creator], "cast": []},
        3: {"title": "丙", "year": "2019", "genres": [], "genreIds": [], "airedEpisodes": None, "creators": [], "cast": []},
        4: {"title": "丁", "year": "2018", "genres": [], "genreIds": [], "airedEpisodes": None, "creators": [], "cast": []},
    }
    ratings = AsyncMock(return_value={"tt0000101": {"imdbRating": 8.7, "imdbVotes": 1000, "rottenTomatoes": 95, "metacritic": None}})

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def begin(self):
            return self

    with (
        patch.object(shows, "load_show_details", AsyncMock(return_value=details)),
        patch.object(shows.omdb, "ratings", ratings),
        patch.object(shows, "async_db_session", Session),
    ):
        section = asyncio.run(shows.build_show_section(history))
    ShowSection.model_validate(section)
    assert (section["watchedCount"], section["episodes"], section["ratingCount"]) == (2, 35, 3)
    assert [(i["title"], i["watchedAt"], i["episodesWatched"], i["episodesAired"], i["plays"], i["myRating"]) for i in section["items"]] == [
        ("甲", "2026-10-01", 24, 24, 30, 9),
        ("乙", "2025-01-01", 5, 5, 5, 8.5),
        ("丙", "2024-01-01", 0, None, 0, 6),
        ("丁", "2023-01-01", 0, None, 0, None),
    ]
    first = section["items"][0]
    assert (first["imdbRating"], first["rottenTomatoes"], first["publicScore"], first["director"], first["releaseDate"]) == (8.7, 95, 8.5, "Maker", "2024-01-05")
    assert [(s["number"], s["episodes"], s["publicScore"], s["myRating"]) for s in first["seasons"]] == [(1, 12, 8.1, 7), (2, 12, None, None)]
    # Mean 7.83 (whole-show ratings only): Maker's two shows average 8.75, so the creator is a favourite.
    assert [(p["role"], p["name"], p["count"]) for p in section["people"]] == [("creator", "Maker", 2)]
    assert section["distribution"][8] == 2 and section["distribution"][5] == 1 and sum(section["distribution"]) == 3
    assert asyncio.run(shows.build_show_section({"watchedShows": [], "showRatings": []})) is None


def test_anime_series_have_their_own_section():
    def entry(tmdb, genres):
        return {"plays": 1, "lastWatchedAt": "2026-01-01T00:00:00.000Z", "show": {"title": str(tmdb), "ids": {"tmdb": tmdb}, "genres": genres}, "episodes": 1}

    history = {"watchedShows": [entry(1, ["anime", "animation"]), entry(2, ["animation", "comedy"]), entry(3, ["drama"])], "showRatings": []}
    details = {i: {"title": str(i), "genres": [], "genreIds": [], "creators": [], "cast": []} for i in (1, 2, 3)}
    with patch.object(shows, "load_show_details", AsyncMock(return_value=details)):
        regular = asyncio.run(shows.build_show_section(history))
        anime = asyncio.run(shows.build_show_section(history, anime=True))
    # Western animation (no "anime" tag) stays with the regular shows.
    assert [i["id"] for i in regular["items"]] == [2, 3]
    assert [i["id"] for i in anime["items"]] == [1]
    assert asyncio.run(shows.build_show_section({"watchedShows": [entry(3, ["drama"])], "showRatings": []}, anime=True)) is None
