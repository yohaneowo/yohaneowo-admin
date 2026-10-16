"""The statistics sync's pure parts (ported from the bot's tests/movie-statistics.test.js).

Field-for-field parity with the JavaScript version was checked against real data when the sync moved
here; these keep the rules from drifting.
"""

from app.plugin.module_bot.movie.sync.common import js_round
from app.plugin.module_bot.movie.sync.history import combine_show_ratings
from app.plugin.module_bot.movie.sync.statistics import build_statistics, depth_of
from app.plugin.module_bot.movie.sync.taste import slim_details, taste_json


def taste(**overrides):
    base = {"mean": 7, "showMean": None, "showRatingCount": 0, "knownCount": 0, "genres": {}, "historyItems": [], "timeline": [], "seenIds": set(), "ratings": [], "people": {}}
    return {**base, **overrides}


def test_js_round_rounds_halves_up():
    assert js_round(8.25 * 10) / 10 == 8.3
    assert js_round(-1.5) == -1
    assert js_round(2.5) == 3


def test_empty_history_has_no_invented_average_or_preferences():
    result = build_statistics({"ratings": [], "watched": []}, taste(), {})
    assert result["mean"] is None
    assert result["plays"] == 0
    assert result["films"] == []
    assert result["distribution"] == [0] * 10
    assert result["people"] == []


def test_full_totals_sampled_preferences_and_separate_roles():
    person = {"id": 123, "name": "Test person", "photo": None}
    details = {90001: {"title": "First", "directors": [person], "cast": [person]}, 90002: {"title": "Second", "directors": [person], "cast": []}}
    history = {"ratings": [{"rating": 9}, {"rating": 10}, {"rating": 2}], "watched": [{"plays": 3}, {"plays": 1}]}
    result = build_statistics(
        history,
        taste(
            knownCount=2,
            genres={1: {"name": "Drama", "count": 2, "ratedCount": 2, "weight": 5}},
            historyItems=[{"type": "movie", "id": 90001, "rating": 9}, {"type": "movie", "id": 90002, "rating": 10}],
        ),
        details,
    )
    assert result["ratingCount"] == 3
    assert result["plays"] == 4
    assert result["distribution"][1] == 1
    assert result["sampleCount"] == 2
    assert result["genres"][0]["average"] == 9.5
    assert [(p["role"], p["count"]) for p in result["people"]] == [("director", 2)]
    assert result["films"] == []


def test_recent_watches_become_profile_films_with_public_scores():
    details = {
        90003: {
            "title": "Sample",
            "year": "2024",
            "releaseDate": "2024-01-02",
            "genres": ["剧情", "犯罪"],
            "directors": [{"id": 7, "name": "Director"}],
            "cast": [],
            "country": "日本",
            "voteAverage": 8.25,
            "voteCount": 1200,
            "poster": "https://image.tmdb.org/t/p/w185/abc.jpg",
        }
    }
    movie = {"ids": {"tmdb": 90003, "imdb": "tt0000003"}}
    history = {"ratings": [{"rating": 9, "movie": movie}, {"rating": 6, "movie": {"ids": {"tmdb": 5}}}], "watched": [{"plays": 1, "movie": movie}]}
    timeline = [
        {"id": 90003, "title": "Sample", "rating": 9, "watchedAt": "2026-10-01"},
        {"id": 1, "title": "Undated", "rating": 8, "watchedAt": None},
    ]
    extras = {"ratings": {"tt0000003": {"imdbRating": 7.6, "imdbVotes": 4200, "rottenTomatoes": 91, "metacritic": "N/A"}}, "depth": {"depth": 0.5, "depthGroups": 2}}
    result = build_statistics(history, taste(timeline=timeline), details, extras)
    [film] = result["films"]
    assert (film["director"], film["directorId"], film["year"]) == ("Director", 7, 2024)
    assert film["publicScore"] == 8.3
    assert (film["imdbId"], film["imdbRating"], film["rottenTomatoes"], film["metacritic"]) == ("tt0000003", 7.6, 91, None)
    assert film["genres"] == ["剧情", "犯罪"]
    assert film["poster"] == "https://image.tmdb.org/t/p/w185/abc.jpg"
    assert result["ratedIds"] == [90003, 5]
    assert result["depth"] == 0.5


def test_depth_averages_directors_seen_twice_and_series_started():
    seen = {1, 2, 10}
    directed = {"7": [1, 2, 3, 4], "8": [1, 5]}
    series = {"20": [10, 11], "21": [12]}
    # Director 7: 2/4, director 8 only one film seen, series 20: 1/2, series 21 not started.
    assert depth_of(seen, directed, series) == {"depth": 0.5, "depthGroups": 2}
    assert depth_of(set(), {}, {}) == {"depth": None, "depthGroups": 0}


def test_show_ratings_average_seasons_and_fall_back_to_the_whole_show():
    show = {"ids": {"trakt": 1}, "title": "A"}
    other = {"ids": {"trakt": 2}, "title": "B"}
    combined = combine_show_ratings(
        [{"show": show, "rating": 8, "rated_at": "2026-01-01"}, {"show": show, "rating": 6, "rated_at": "2026-03-01"}],
        [{"show": show, "rating": 1, "rated_at": "2026-02-01"}, {"show": other, "rating": 9, "rated_at": "2025-01-01"}],
    )
    assert [(c["show"]["title"], c["rating"], c["seasons"], c["ratedAt"]) for c in combined] == [("A", 7, 2, "2026-03-01"), ("B", 9, 1, "2025-01-01")]


def test_slim_details_keeps_what_the_statistics_need():
    raw = {
        "title": "片名",
        "original_title": "Title",
        "release_date": "",
        "genres": [{"id": 18, "name": "剧情"}],
        "production_countries": [{"name": "Japan"}],
        "vote_average": 7,
        "vote_count": 10,
        "poster_path": "/p.jpg",
        "belongs_to_collection": None,
        "credits": {"crew": [{"id": 1, "name": "D", "original_name": "Dee", "job": "Director", "profile_path": None}], "cast": [{"id": n, "name": str(n)} for n in range(7)]},
        "translations": {"translations": [{"iso_639_1": "ja", "data": {"title": "題名"}}, {"iso_639_1": "en", "data": {"title": "English Title"}}]},
    }
    slim = slim_details(raw)
    # JavaScript: "".slice(0, 4) is "" (not null), "" || null is null.
    assert (slim["year"], slim["releaseDate"], slim["collectionId"]) == ("", None, None)
    assert slim["poster"] == "https://image.tmdb.org/t/p/w185/p.jpg"
    assert slim["directors"] == [{"id": 1, "name": "D", "originalName": "Dee", "photo": None}] and len(slim["cast"]) == 5
    assert (slim["cacheVersion"], slim["titleEn"]) == (7, "English Title")
    # English originals have no English translation title.
    assert slim_details({**raw, "translations": None, "original_language": "en"})["titleEn"] == "Title"


def test_taste_json_resolves_titles_for_the_bot():
    t = taste(
        ratings=[{"rating": 8, "movie": {"ids": {"tmdb": 3}, "title": "Trakt title"}}],
        seenIds={5, 3},
        genres={18: {"name": "剧情", "count": 1, "weight": 1, "ratedCount": 1}},
        people={9: {"name": "P", "weight": 2, "count": 1, "films": []}},
        ratingCount=1,
        watchedCount=1,
    )
    data = taste_json(t, {3: {"title": "TMDB title"}})
    assert data["ratings"] == [{"tmdb": 3, "title": "TMDB title", "rating": 8}]
    assert data["seenIds"] == [3, 5]
    assert data["genres"] == [[18, {"name": "剧情", "count": 1, "weight": 1, "ratedCount": 1}]]


def test_watch_dates_come_from_watches_not_ratings():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from app.plugin.module_bot.movie.sync import taste as taste_module

    def movie(tmdb, title):
        return {"ids": {"tmdb": tmdb}, "title": title}

    history = {
        # Rated in bulk today; really watched years apart.
        "ratings": [{"rating": 9, "ratedAt": "2026-10-15T08:00:00.000Z", "movie": movie(1, "Old")}, {"rating": 7, "ratedAt": "2026-10-15T08:00:01.000Z", "movie": movie(2, "Unwatched")}],
        "watched": [{"plays": 1, "lastWatchedAt": "2019-05-01T20:00:00.000Z", "movie": movie(1, "Old")}, {"plays": 1, "lastWatchedAt": "2024-01-02T20:00:00.000Z", "movie": movie(3, "Newer")}],
        "showRatings": [],
    }
    with patch.object(taste_module, "load_details", AsyncMock(return_value={})):
        built, _ = asyncio.run(taste_module.build_taste(history))
    # Rated but never marked watched keeps its rating date; the rest use the watch.
    assert [(item["id"], item["watchedAt"]) for item in built["timeline"]] == [(2, "2026-10-15"), (3, "2024-01-02"), (1, "2019-05-01")]
