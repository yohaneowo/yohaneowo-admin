"""The website's 推荐 page: both kinds of picks in one shape, today's apart from the rest."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

from app.plugin.module_bot.movie import anime_recommend, picks_view, recommend
from app.plugin.module_bot.movie import bonus as bonus_picks


def _pick(day, minute, **extra):
    return SimpleNamespace(
        pick_id=f"{day}-{minute}",
        day=day,
        shown_at=datetime.fromisoformat(day).replace(hour=10, minute=minute),
        mode="comfort",
        reason="r",
        action=None,
        rating=None,
        uses_bonus=False,
        title="fallback",
        **extra,
    )


def test_movie_and_anime_cards_share_one_shape():
    film = _pick(
        "2026-10-19",
        1,
        tmdb_id=27205,
        data={"title": "盗梦空间", "originalTitle": "Inception", "year": 2010, "poster": "p.jpg", "director": "Nolan", "genres": ["科幻"], "traktUrl": "https://trakt.tv/movies/inception-2010"},
    )
    anime = _pick(
        "2026-10-19",
        2,
        anilist_id=1,
        data={"title": "星际牛仔", "titleNative": "カウボーイビバップ", "year": 1998, "cover": "c.jpg", "studios": ["Sunrise"], "links": {"bangumi": None, "anilist": "https://anilist.co/anime/1"}},
    )
    a = picks_view._out(film, "movie")  # noqa: SLF001
    b = picks_view._out(anime, "anime")  # noqa: SLF001
    assert set(a) == set(b)
    assert (a["title"], a["poster"], a["people"], a["url"]) == ("盗梦空间", "p.jpg", ["Nolan"], "https://trakt.tv/movies/inception-2010")
    assert (b["originalTitle"], b["poster"], b["people"], b["url"]) == ("カウボーイビバップ", "c.jpg", ["Sunrise"], "https://anilist.co/anime/1")
    # No card data: the stored title and a TMDB link.
    bare = picks_view._out(_pick("2026-10-19", 3, tmdb_id=5, data=None), "movie")  # noqa: SLF001
    assert (bare["title"], bare["url"]) == ("fallback", "https://www.themoviedb.org/movie/5")


def test_today_is_oldest_first_and_the_30_days_newest_first(monkeypatch):
    picks = [_pick("2026-10-19", 5, tmdb_id=1, data={}), _pick("2026-10-18", 9, tmdb_id=2, data={}), _pick("2026-10-19", 1, tmdb_id=3, data={}), _pick("2026-10-17", 1, tmdb_id=4, data={})]

    async def recent(db, profile_id):
        return picks

    async def remaining(db, row):
        return 1

    async def left(db, profile_id, feature):
        return 100

    monkeypatch.setattr(recommend, "_recent_picks", recent)
    monkeypatch.setattr(recommend, "_remaining", remaining)
    monkeypatch.setattr(recommend, "today_taipei", lambda: SimpleNamespace(isoformat=lambda: "2026-10-19"))
    monkeypatch.setattr(bonus_picks, "left", left)
    row = SimpleNamespace(profile_id="p", discord_id=None, pick_quota=None)
    out = asyncio.run(picks_view.view(None, row, "movie"))
    assert [p["pickId"] for p in out["todayPicks"]] == ["2026-10-19-1", "2026-10-19-5"]
    # The 30 days include today.
    assert [p["pickId"] for p in out["recent"]] == ["2026-10-19-5", "2026-10-19-1", "2026-10-18-9", "2026-10-17-1"]
    assert (out["quota"], out["remainingToday"], out["bonus"], out["discordLinked"]) == (3, 1, 100, False)
    assert anime_recommend.quota_for(SimpleNamespace(anime_pick_quota=None)) == 5
