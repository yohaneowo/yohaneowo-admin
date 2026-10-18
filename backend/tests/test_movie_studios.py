"""The studio card: info, the works list built page by page, matching, scores and outages."""

import asyncio
from contextlib import ExitStack
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


def _page(*nodes, more=False, name="MADHOUSE"):
    return {"Studio": {"id": 11, "name": name, "media": {"pageInfo": {"hasNextPage": more}, "nodes": list(nodes)}}}


def _node(anilist_id, native, year, mal_id=None, fmt="TV", english=None):
    return {"id": anilist_id, "idMal": mal_id, "type": "ANIME", "format": fmt, "title": {"native": native, "romaji": native, "english": english}, "startDate": {"year": year}}


class World:
    """Fake sources and cache; `run(coro)` runs one scenario in one event loop."""

    def __init__(self, pages=(), person=None, subjects=(), subject=None, search=None, mal=None, bangumi_down=False):
        self.store = {}
        down = AsyncMock(side_effect=SourceUnavailable("down"))
        page_list = list(pages)

        async def anilist_query(db, text, variables):
            if text == studios.NAME_QUERY:
                return page_list[0] if page_list else {"Studio": None}
            index = variables["page"] - 1
            return page_list[index] if index < len(page_list) else {"Studio": None}

        self.anilist = AsyncMock(side_effect=anilist_query)
        self.person = down if bangumi_down else AsyncMock(return_value=person)
        self.subjects = down if bangumi_down else AsyncMock(return_value=list(subjects))
        self.subject = down if bangumi_down else AsyncMock(side_effect=lambda db, sid: (subject or {}).get(sid))
        self.search = down if bangumi_down else AsyncMock(side_effect=lambda db, keyword, low, high: (search or {}).get(keyword, []))
        mal_scores = mal or {}

        async def fake_mal(ids, deadline):
            return {str(i): mal_scores[str(i)] for i in ids if str(i) in mal_scores}, False

        self.mal = AsyncMock(side_effect=fake_mal)

    async def read_cache(self, namespace, keys):
        return {k: self.store[(namespace, k)] for k in keys if (namespace, k) in self.store}

    async def write_cache(self, namespace, items):
        self.store.update({(namespace, k): v for k, v in items.items()})

    def run(self, coro):
        studios._building.clear()
        studios._partial.clear()
        studios._filling.clear()
        studios._missing.clear()
        with ExitStack() as stack:
            for target, name, value in (
                (studios, "read_cache", self.read_cache),
                (studios, "write_cache", self.write_cache),
                (studios, "async_db_session", _Session),
                (studios.anilist, "query", self.anilist),
                (studios.bangumi, "person", self.person),
                (studios.bangumi, "person_subjects", self.subjects),
                (studios.bangumi, "subject", self.subject),
                (studios.bangumi, "search", self.search),
                (studios, "_mal_scores", self.mal),
            ):
                stack.enter_context(patch.object(target, name, value))
            return asyncio.run(coro)


async def _settle():
    """Let the build and the background top-up finish."""
    for task in [*studios._building.values(), *studios._filling.values()]:
        await task
    for task in list(studios._filling.values()):
        await task


def test_info_is_bangumis_company_entry():
    world = World(pages=[_page()], person={"name": "MADHOUSE", "summary": " 日本的动画制作公司。 ", "images": {"large": "https://lain.bgm.tv/pic/crt/l/f3/80/603.jpg"}})

    async def scenario():
        return await studios.info(11, 603, "zh"), await studios.info(11, 603, "en")

    zh, en = world.run(scenario())
    assert zh == {"name": "MADHOUSE", "logo": "https://lain.bgm.tv/pic/crt/l/f3/80/603.jpg", "description": "日本的动画制作公司。"}
    assert en["description"] is None
    # The second look came from the cache.
    assert world.person.await_count == 1


def test_works_show_up_while_building_then_complete_with_scores():
    world = World(
        pages=[
            _page(_node(1, "DEATH NOTE", 2006, mal_id=1535, english="Death Note"), more=True),
            _page(_node(2, "ワンパンマン", 2015, mal_id=30276, english="One Punch Man"), {**_node(3, "MV", 2020), "format": "MUSIC"}),
        ],
        # The Chinese title comes from the studio's list; the Bangumi score from each entry.
        subjects=[{"id": 1, "type": 2, "name": "DEATH NOTE", "name_cn": "死亡笔记", "staff": "动画制作"}, {"id": 9, "type": 1, "name": "ワンパンマン", "staff": "原作"}],
        subject={1: {"date": "2006-10-03", "rating": {"score": 8.1}}},
        mal={"1535": {"score": 8.62}},
    )

    async def scenario():
        first = await studios.works(11, 603, "zh")
        await _settle()
        return first, await studios.works(11, 603, "zh"), await studios.works(11, 603, "en")

    first, done, en = world.run(scenario())
    # The first answer doesn't wait for the list.
    assert first["building"] is True and first["scoresPending"] is True
    assert [(w["title"], w["year"], w["bangumiScore"], w["malScore"]) for w in done["works"]] == [
        ("ワンパンマン", 2015, None, None),
        ("死亡笔记", 2006, 8.1, 8.62),
    ]
    assert done["building"] is False and done["scoresPending"] is False
    assert [w["title"] for w in en["works"]] == ["One Punch Man", "Death Note"]


def test_unmatched_works_are_searched_by_title_and_year():
    world = World(
        pages=[_page(_node(1, "ワンパンマン OVA", 2015, mal_id=30276))],
        subjects=[],
        search={"ワンパンマン OVA": [{"id": 77, "name": "ワンパンマン OVA", "name_cn": "一拳超人 OVA", "rating": {"score": 7.4}, "date": "2015-12-01"}]},
        mal={"30276": {"score": 7.8}},
    )

    async def scenario():
        await studios.works(11, 603, "zh")
        await _settle()
        return await studios.works(11, 603, "zh")

    done = world.run(scenario())
    work = done["works"][0]
    assert (work["title"], work["bangumiId"], work["bangumiScore"], work["malScore"]) == ("一拳超人 OVA", 77, 7.4, 7.8)
    assert world.search.await_args.args[2:] == ("2014-01-01", "2016-12-31")


def test_bangumi_only_studio_lists_its_animation_entries():
    world = World(
        pages=[],
        subjects=[
            {"id": 5, "type": 2, "name": "作品A", "name_cn": "作品甲", "staff": "动画制作"},
            {"id": 6, "type": 2, "name": "作品B", "staff": "制作协力"},
        ],
        subject={5: {"date": "2019-01-01", "rating": {"score": 7.2}}},
    )

    async def scenario():
        await studios.works(None, 22498, "zh")
        await _settle()
        return await studios.works(None, 22498, "zh")

    done = world.run(scenario())
    assert [(w["title"], w["year"], w["bangumiScore"]) for w in done["works"]] == [("作品甲", 2019, 7.2)]


def test_bangumi_down_still_lists_anilist_works_and_retries_sooner():
    world = World(pages=[_page(_node(1, "DEATH NOTE", 2006, mal_id=1535))], bangumi_down=True, mal={"1535": {"score": 8.62}})

    async def scenario():
        await studios.works(11, 603, "zh")
        await _settle()
        return await studios.works(11, 603, "zh")

    done = world.run(scenario())
    assert [(w["title"], w["malScore"], w["bangumiScore"]) for w in done["works"]] == [("DEATH NOTE", 8.62, None)]
    assert world.store[("studio_card_v3", "11:603")]["partial"] is True


def test_unknown_studio_is_none():
    world = World(pages=[{"Studio": None}], subjects=[])

    async def scenario():
        await studios.works(11, 603, "zh")
        await _settle()
        return await studios.works(11, 603, "zh")

    assert world.run(scenario()) is None


def test_titles_match_despite_width_numerals_and_punctuation():
    assert studios._norm("チ。-地球の運動について-") == studios._norm("チ。 ―地球の運動について―")
    assert studios._norm("オーバーロードⅣ") == studios._norm("オーバーロードIV")
    assert studios._same_title("ダイヤのA[エース]", "ダイヤのA") is True
    assert studios._same_title("A", "AB") is False
