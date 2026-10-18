"""The studio card: AniList works matched to Bangumi, scores, the Bangumi-only case and outages."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.plugin.module_bot.movie import studios
from app.plugin.module_bot.movie.sources.base import SourceUnavailable


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return self


def _anilist(*nodes):
    return {
        "Studio": {
            "id": 11,
            "name": "MADHOUSE",
            "media": {"nodes": list(nodes)},
        }
    }


def _node(anilist_id, native, year, mal_id=None, fmt="TV", english=None):
    return {"id": anilist_id, "idMal": mal_id, "type": "ANIME", "format": fmt, "title": {"native": native, "romaji": native, "english": english}, "startDate": {"year": year}}


def _run(anilist_data=None, person=None, subjects=(), subject=None, mal=None, bangumi_down=False, cache=None, search=None):
    store = dict(cache or {})

    async def read_cache(namespace, keys):
        return {k: store[(namespace, k)] for k in keys if (namespace, k) in store}

    async def write_cache(namespace, items):
        store.update({(namespace, k): v for k, v in items.items()})

    down = AsyncMock(side_effect=SourceUnavailable("down"))
    calls = {
        "anilist": AsyncMock(return_value=anilist_data),
        "person": down if bangumi_down else AsyncMock(return_value=person),
        "subjects": down if bangumi_down else AsyncMock(return_value=list(subjects)),
        "subject": down if bangumi_down else AsyncMock(side_effect=lambda db, sid: (subject or {}).get(sid)),
        "search": down if bangumi_down else AsyncMock(side_effect=lambda db, keyword, low, high: (search or {}).get(keyword, [])),
        "mal": AsyncMock(return_value=(mal or {}, False)),
    }
    with (
        patch.object(studios, "read_cache", read_cache),
        patch.object(studios, "write_cache", write_cache),
        patch.object(studios, "async_db_session", _Session),
        patch.object(studios.anilist, "query", calls["anilist"]),
        patch.object(studios.bangumi, "person", calls["person"]),
        patch.object(studios.bangumi, "person_subjects", calls["subjects"]),
        patch.object(studios.bangumi, "subject", calls["subject"]),
        patch.object(studios.bangumi, "search", calls["search"]),
        patch.object(studios, "_mal_scores", calls["mal"]),
    ):
        zh = asyncio.run(studios.lookup(11, 603, "zh"))
        en = asyncio.run(studios.lookup(11, 603, "en"))
    return zh, en, calls, store


def test_works_from_anilist_with_bangumi_titles_and_scores():
    zh, en, calls, _ = _run(
        anilist_data=_anilist(
            _node(1, "DEATH NOTE", 2006, mal_id=1535, english="Death Note"),
            _node(2, "ワンパンマン", 2015, mal_id=30276, english="One Punch Man"),
            # Not an anime series or film: left out.
            {**_node(3, "Music Video", 2020), "format": "MUSIC"},
        ),
        person={"name": "MADHOUSE", "summary": " 日本的动画制作公司。 ", "images": {"large": "https://lain.bgm.tv/pic/crt/l/f3/80/603.jpg"}},
        # The Chinese title comes from this list; the Bangumi score from each entry.
        subjects=[{"id": 1, "type": 2, "name": "DEATH NOTE", "name_cn": "死亡笔记", "staff": "动画制作"}, {"id": 9, "type": 1, "name": "ワンパンマン", "staff": "原作"}],
        subject={1: {"date": "2006-10-03", "rating": {"score": 8.1}}},
        mal={"1535": {"score": 8.62}},
    )
    assert zh["name"] == "MADHOUSE"
    assert zh["logo"].endswith("603.jpg")
    assert zh["description"] == "日本的动画制作公司。"
    # Newest first; One Punch Man has no Bangumi anime entry (only the manga) so no Bangumi score.
    assert [(w["title"], w["year"], w["bangumiScore"], w["malScore"]) for w in zh["works"]] == [
        ("ワンパンマン", 2015, None, None),
        ("死亡笔记", 2006, 8.1, 8.62),
    ]
    assert [w["title"] for w in en["works"]] == ["One Punch Man", "Death Note"]
    assert en["description"] is None
    # The English look came from the cache.
    assert calls["anilist"].await_count == 1


def test_bangumi_only_studio_lists_its_animation_entries():
    zh, _, _, _ = _run(
        anilist_data=None,
        person={"name": "ゼロジー", "summary": "", "images": {"large": ""}},
        subjects=[
            {"id": 5, "type": 2, "name": "作品A", "name_cn": "作品甲", "staff": "动画制作"},
            {"id": 6, "type": 2, "name": "作品B", "staff": "制作协力"},
        ],
        subject={5: {"date": "2019-01-01", "rating": {"score": 7.2}}},
    )
    assert [(w["title"], w["year"], w["bangumiScore"]) for w in zh["works"]] == [("作品甲", 2019, 7.2)]
    assert zh["logo"] is None and zh["description"] is None


def test_bangumi_down_still_shows_anilist_works_and_retries_sooner():
    zh, _, _, store = _run(anilist_data=_anilist(_node(1, "DEATH NOTE", 2006, mal_id=1535)), bangumi_down=True, mal={"1535": {"score": 8.62}})
    assert [(w["title"], w["malScore"], w["bangumiScore"]) for w in zh["works"]] == [("DEATH NOTE", 8.62, None)]
    assert store[("studio_card_v3", "11:603")]["partial"] is True


def test_unknown_studio_is_none():
    zh, _, _, _ = _run(anilist_data={"Studio": None}, person=None, subjects=[])
    assert zh is None


def test_every_page_of_works_is_listed():
    pages = [
        {"Studio": {"id": 11, "name": "MADHOUSE", "media": {"pageInfo": {"hasNextPage": True}, "nodes": [_node(1, "A", 2020)]}}},
        {"Studio": {"id": 11, "name": "MADHOUSE", "media": {"pageInfo": {"hasNextPage": False}, "nodes": [_node(2, "B", 2010), _node(3, "C", 2015)]}}},
    ]
    zh, _, calls, _ = _run(anilist_data=None, person={"name": "MADHOUSE"}, subjects=[])
    with (
        patch.object(studios.anilist, "query", AsyncMock(side_effect=pages)),
        patch.object(studios, "async_db_session", _Session),
    ):
        name, works = asyncio.run(studios._anilist_works(11))
    assert name == "MADHOUSE"
    assert [w["anilistId"] for w in works] == [1, 2, 3]


def test_scores_left_over_continue_in_the_background():
    scheduled = []
    with (
        patch.object(studios, "REQUEST_SECONDS", -1),
        patch.object(studios, "MIN_TOP_UP_SECONDS", -1),
        patch.object(studios, "_fill_later", lambda key, works: scheduled.append((key, len(works)))),
    ):
        zh, _, _, _ = _run(
            anilist_data=_anilist(_node(1, "DEATH NOTE", 2006, mal_id=1535)),
            person={"name": "MADHOUSE"},
            subjects=[{"id": 1, "type": 2, "name": "DEATH NOTE", "name_cn": "死亡笔记", "staff": "动画制作"}],
            subject={1: {"date": "2006-10-03", "rating": {"score": 8.1}}},
            mal={},
        )
    # Out of time before any Bangumi score: the card says so and a background run takes over.
    assert zh["scoresPending"] is True
    assert zh["works"][0]["title"] == "死亡笔记" and zh["works"][0]["bangumiScore"] is None
    assert scheduled and scheduled[0] == ("11:603", 1)


def test_titles_match_despite_width_numerals_and_punctuation():
    assert studios._norm("チ。-地球の運動について-") == studios._norm("チ。 ―地球の運動について―")
    assert studios._norm("オーバーロードⅣ") == studios._norm("オーバーロードIV")
    assert studios._same_title("ダイヤのA[エース]", "ダイヤのA") is True
    assert studios._same_title("A", "AB") is False


def test_unmatched_works_are_searched_by_title_and_year():
    zh, _, calls, store = _run(
        anilist_data=_anilist(_node(1, "ワンパンマン OVA", 2015, mal_id=30276)),
        person={"name": "MADHOUSE"},
        # The studio's own list doesn't have it under that title.
        subjects=[],
        search={"ワンパンマン OVA": [{"id": 77, "name": "ワンパンマン OVA", "name_cn": "一拳超人 OVA", "rating": {"score": 7.4}, "date": "2015-12-01"}]},
        mal={"30276": {"score": 7.8}},
    )
    work = zh["works"][0]
    assert (work["title"], work["bangumiId"], work["bangumiScore"], work["malScore"]) == ("一拳超人 OVA", 77, 7.4, 7.8)
    # The search ran once: the match and the score are cached (the English look reused them).
    assert calls["search"].await_count == 1
    assert calls["search"].await_args.args[2:] == ("2014-01-01", "2016-12-31")
    assert store[("studio_work_bangumi", "1")]["bangumiId"] == 77


def test_a_cached_card_answers_quickly_and_leaves_scores_to_the_background():
    scheduled = []
    built = {
        "name": "MADHOUSE",
        "nameEn": "MADHOUSE",
        "logo": None,
        "description": None,
        "works": [{"anilistId": 1, "malId": 1535, "bangumiId": 1, "native": "DEATH NOTE", "title": "死亡笔记", "titleEn": "Death Note", "year": 2006}],
        "partial": False,
        "fetchedAt": studios._now(),
    }
    with (
        patch.object(studios, "CACHED_TOP_UP_SECONDS", -1),
        patch.object(studios, "_fill_later", lambda key, works: scheduled.append(key)),
    ):
        zh, _, calls, _ = _run(cache={("studio_card_v3", "11:603"): built}, subject={1: {"rating": {"score": 8.1}}}, mal={})
    # Nothing rebuilt, no score fetched in the request; the background takes over.
    assert calls["anilist"].await_count == 0 and calls["subject"].await_count == 0
    assert zh["works"][0]["title"] == "死亡笔记" and zh["scoresPending"] is True
    assert scheduled == ["11:603", "11:603"]
