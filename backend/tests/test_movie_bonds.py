"""羁绊: common watched titles and seasons between two profiles."""

from types import SimpleNamespace

from app.plugin.module_bot.movie import bonds


def _anime(show_id: int, episodes_watched: int, seasons: list[int]) -> dict:
    return {"id": show_id, "episodesWatched": episodes_watched, "seasons": [{"number": n + 1, "episodes": count} for n, count in enumerate(seasons)]}


def test_watched_seasons_fill_in_order():
    # 12 + 12 episodes: 24 watched reaches both seasons, 13 reaches into the second, 12 only the first.
    assert bonds.watched_seasons(_anime(1, 24, [12, 12])) == {(1, 1), (1, 2)}
    assert bonds.watched_seasons(_anime(1, 13, [12, 12])) == {(1, 1), (1, 2)}
    assert bonds.watched_seasons(_anime(1, 12, [12, 12])) == {(1, 1)}
    assert bonds.watched_seasons(_anime(1, 0, [12, 12])) == set()
    # Unknown episode counts: credit the first season.
    assert bonds.watched_seasons(_anime(1, 5, [0, 0])) == {(1, 1)}


def test_overlap_counts_by_kind_and_share_of_mine():
    me = SimpleNamespace(
        profile_id="a",
        discord_id="1",
        trakt_user="a",
        trakt_name="A",
        snapshot={
            "films": [{"id": 10}, {"id": 11}, {"id": 12}],
            "shows": {"items": [{"id": 20}]},
            "anime": {"items": [_anime(30, 24, [12, 12]), _anime(31, 12, [12])]},
        },
    )
    friend = SimpleNamespace(
        profile_id="b",
        discord_id="2",
        trakt_user="b",
        trakt_name="B",
        snapshot={
            "films": [{"id": 11}, {"id": 12}, {"id": 13}],
            "shows": {"items": [{"id": 21}]},
            "anime": {"items": [_anime(30, 12, [12, 12])]},
        },
    )
    rows = bonds.bonds(me, [friend, me])
    assert len(rows) == 1
    row = rows[0]
    assert row["discordId"] == "2"
    assert row["movies"] == {"common": 2, "mine": 3, "theirs": 3}
    assert row["shows"] == {"common": 0, "mine": 1, "theirs": 1}
    # Seasons: I watched 30/S1, 30/S2, 31/S1; they watched 30/S1 only.
    assert row["animeSeasons"] == {"common": 1, "mine": 3, "theirs": 1}
    assert row["total"] == {"common": 3, "mine": 7, "share": round(3 / 7, 4)}
