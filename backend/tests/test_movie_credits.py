"""Anime credit cards: info, works built page by page per kind, matching, scores, voice actor
identities and outages."""

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from app.plugin.module_bot.movie import credits
from app.plugin.module_bot.movie.credits import Who
from app.plugin.module_bot.movie.sources.base import SourceUnavailable

STUDIO = Who("studio", 11, 603)


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return self


def _node(anilist_id, native, year, mal_id=None, fmt="TV", english=None):
    return {"id": anilist_id, "idMal": mal_id, "type": "ANIME", "format": fmt, "title": {"native": native, "romaji": native, "english": english}, "startDate": {"year": year}}


def _studio_page(*nodes, more=False):
    return {"Studio": {"media": {"pageInfo": {"hasNextPage": more}, "nodes": list(nodes)}}}


def _staff_page(*edges, more=False):
    return {"Staff": {"staffMedia": {"pageInfo": {"hasNextPage": more}, "edges": list(edges)}}}


def _voice_page(*edges, more=False):
    return {"Staff": {"characterMedia": {"pageInfo": {"hasNextPage": more}, "edges": list(edges)}}}


class World:
    """Fake sources and cache; `run(coro)` runs one scenario in one event loop."""

    def __init__(
        self,
        pages=(),
        person=None,
        subjects=(),
        characters=(),
        subject=None,
        search=None,
        mal=None,
        bangumi_down=False,
        staff=None,
        studio_name=None,
        staff_search=None,
        person_search=None,
        tmdb=None,
    ):
        self.store = {}
        down = AsyncMock(side_effect=SourceUnavailable("down"))
        page_list = list(pages)

        async def anilist_query(db, text, variables):
            if text == credits.STUDIO_INFO_QUERY:
                return {"Studio": {"name": studio_name} if studio_name else None}
            if text == credits.STAFF_INFO_QUERY:
                return {"Staff": staff}
            if text == credits.STAFF_SEARCH_QUERY:
                return {"Page": {"staff": (staff_search or {}).get(variables["s"], [])}}
            index = variables["page"] - 1
            return page_list[index] if index < len(page_list) else {}

        self.anilist = AsyncMock(side_effect=anilist_query)
        self.person = down if bangumi_down else AsyncMock(return_value=person)
        self.subjects = down if bangumi_down else AsyncMock(return_value=list(subjects))
        self.characters = down if bangumi_down else AsyncMock(return_value=list(characters))
        self.subject = down if bangumi_down else AsyncMock(side_effect=lambda db, sid: (subject or {}).get(sid))
        self.search = down if bangumi_down else AsyncMock(side_effect=lambda db, keyword, low, high: (search or {}).get(keyword, []))
        self.search_persons = down if bangumi_down else AsyncMock(side_effect=lambda db, keyword: (person_search or {}).get(keyword, []))
        self.tmdb = AsyncMock(return_value=(200, tmdb) if tmdb else (404, None))
        mal_scores = mal or {}

        async def fake_mal(ids, deadline):
            return {str(i): mal_scores[str(i)] for i in ids if str(i) in mal_scores}, False

        self.mal = AsyncMock(side_effect=fake_mal)

    async def read_cache(self, namespace, keys):
        return {k: self.store[(namespace, k)] for k in keys if (namespace, k) in self.store}

    async def write_cache(self, namespace, items):
        self.store.update({(namespace, k): v for k, v in items.items()})

    def run(self, coro):
        for registry in (credits._building, credits._partial, credits._filling, credits._missing, credits._resolving):
            registry.clear()
        with ExitStack() as stack:
            for target, name, value in (
                (credits, "read_cache", self.read_cache),
                (credits, "write_cache", self.write_cache),
                (credits, "async_db_session", _Session),
                (credits, "tmdb_get", self.tmdb),
                (credits.anilist, "query", self.anilist),
                (credits.bangumi, "person", self.person),
                (credits.bangumi, "person_subjects", self.subjects),
                (credits.bangumi, "person_characters", self.characters),
                (credits.bangumi, "subject", self.subject),
                (credits.bangumi, "search", self.search),
                (credits.bangumi, "search_persons", self.search_persons),
                (credits, "_mal_scores", self.mal),
            ):
                stack.enter_context(patch.object(target, name, value))
            return asyncio.run(coro)


async def _settle():
    """Let the build and the background top-up finish."""
    for task in [*credits._building.values(), *credits._filling.values()]:
        await task
    for task in list(credits._filling.values()):
        await task


async def _finished(who, lang="zh"):
    await credits.works(who, lang)
    await _settle()
    return await credits.works(who, lang)


# ── info ──


def test_studio_info_is_bangumis_company_entry():
    world = World(person={"name": "MADHOUSE", "summary": " 日本的动画制作公司。 ", "images": {"large": "https://lain.bgm.tv/pic/crt/l/f3/80/603.jpg"}}, studio_name="MADHOUSE")

    async def scenario():
        return await credits.info(STUDIO, "zh"), await credits.info(STUDIO, "en")

    zh, en = world.run(scenario())
    assert zh == {"name": "MADHOUSE", "image": "https://lain.bgm.tv/pic/crt/l/f3/80/603.jpg", "description": "日本的动画制作公司。", "format": "bbcode"}
    assert en["description"] is None and en["format"] == "markdown"
    # The second look came from the cache.
    assert world.person.await_count == 1


def test_person_info_uses_anilists_photo_and_both_descriptions():
    world = World(
        person={"name": "林祐一郎", "summary": "日本的动画导演。", "images": {"large": "https://lain.bgm.tv/pic/crt/l/1.jpg"}},
        staff={"name": {"full": "Yuuichirou Hayashi", "native": "林祐一郎"}, "description": "A __director__.", "image": {"large": "https://s4.anilist.co/file/anilistcdn/staff/large/n1.png"}},
    )

    async def scenario():
        who = Who("director", 115016, 31113)
        return await credits.info(who, "zh"), await credits.info(who, "en")

    zh, en = world.run(scenario())
    assert zh["image"].endswith("n1.png") and zh["description"] == "日本的动画导演。" and zh["name"] == "林祐一郎"
    assert en == {"name": "Yuuichirou Hayashi", "image": zh["image"], "description": "A __director__.", "format": "markdown"}


# ── studios ──


def test_studio_works_show_up_while_building_then_complete_with_scores():
    world = World(
        pages=[
            _studio_page(_node(1, "DEATH NOTE", 2006, mal_id=1535, english="Death Note"), more=True),
            _studio_page(_node(2, "ワンパンマン", 2015, mal_id=30276, english="One Punch Man"), {**_node(3, "MV", 2020), "format": "MUSIC"}),
        ],
        # The Chinese title comes from the studio's list; the Bangumi score from each entry.
        subjects=[{"id": 1, "type": 2, "name": "DEATH NOTE", "name_cn": "死亡笔记", "staff": "动画制作"}, {"id": 9, "type": 1, "name": "ワンパンマン", "staff": "原作"}],
        subject={1: {"date": "2006-10-03", "rating": {"score": 8.1}}},
        mal={"1535": {"score": 8.62}},
    )

    async def scenario():
        first = await credits.works(STUDIO, "zh")
        await _settle()
        return first, await credits.works(STUDIO, "zh"), await credits.works(STUDIO, "en")

    first, done, en = world.run(scenario())
    # The first answer doesn't wait for the list.
    assert first["building"] is True and first["scoresPending"] is True
    assert [(w["title"], w["year"], w["bangumiScore"], w["malScore"]) for w in done["works"]] == [
        ("ワンパンマン", 2015, None, None),
        ("死亡笔记", 2006, 8.1, 8.62),
    ]
    assert done["building"] is False and done["scoresPending"] is False
    assert [w["title"] for w in en["works"]] == ["One Punch Man", "Death Note"]
    assert "characters" not in done["works"][0]


def test_unmatched_works_are_searched_by_title_and_year():
    world = World(
        pages=[_studio_page(_node(1, "ワンパンマン OVA", 2015, mal_id=30276))],
        search={"ワンパンマン OVA": [{"id": 77, "name": "ワンパンマン OVA", "name_cn": "一拳超人 OVA", "rating": {"score": 7.4}, "date": "2015-12-01"}]},
        mal={"30276": {"score": 7.8}},
    )
    done = world.run(_finished(STUDIO))
    work = done["works"][0]
    assert (work["title"], work["bangumiId"], work["bangumiScore"], work["malScore"]) == ("一拳超人 OVA", 77, 7.4, 7.8)
    assert world.search.await_args.args[2:] == ("2014-01-01", "2016-12-31")


def test_bangumi_only_studio_lists_its_animation_entries():
    world = World(
        subjects=[
            {"id": 5, "type": 2, "name": "作品A", "name_cn": "作品甲", "staff": "动画制作"},
            {"id": 6, "type": 2, "name": "作品B", "staff": "制作协力"},
        ],
        subject={5: {"date": "2019-01-01", "rating": {"score": 7.2}}},
    )
    done = world.run(_finished(Who("studio", None, 22498)))
    assert [(w["title"], w["year"], w["bangumiScore"]) for w in done["works"]] == [("作品甲", 2019, 7.2)]


def test_bangumi_down_still_lists_anilist_works_and_retries_sooner():
    world = World(pages=[_studio_page(_node(1, "DEATH NOTE", 2006, mal_id=1535))], bangumi_down=True, mal={"1535": {"score": 8.62}})
    done = world.run(_finished(STUDIO))
    assert [(w["title"], w["malScore"], w["bangumiScore"]) for w in done["works"]] == [("DEATH NOTE", 8.62, None)]
    assert world.store[("credit_card", STUDIO.key)]["partial"] is True


def test_unknown_card_is_none():
    world = World(pages=[{}])
    assert world.run(_finished(STUDIO)) is None


# ── people ──


def test_a_director_lists_only_directing():
    world = World(
        pages=[
            _staff_page(
                {"staffRole": "Director", "node": _node(1, "進撃の巨人 The Final Season", 2020)},
                {"staffRole": "Storyboard (eps 3, 7)", "node": _node(2, "呪術廻戦", 2020)},
                {"staffRole": "Chief Director", "node": _node(3, "作品C", 2018)},
                # Two credits on one anime list it once.
                {"staffRole": "Director (eps 1-12)", "node": _node(1, "進撃の巨人 The Final Season", 2020)},
            )
        ],
    )
    done = world.run(_finished(Who("director", 115016, None)))
    assert [w["anilistId"] for w in done["works"]] == [1, 3]


def test_an_original_creator_lists_the_adaptations():
    world = World(pages=[_staff_page({"staffRole": "Original Creator", "node": _node(1, "葬送のフリーレン", 2023)}, {"staffRole": "Original Character Design", "node": _node(2, "X", 2020)})])
    done = world.run(_finished(Who("original", 1, None)))
    assert [w["anilistId"] for w in done["works"]] == [1]


def test_bangumi_only_people_are_listed_by_relation():
    world = World(subjects=[{"id": 5, "type": 2, "name": "作品A", "name_cn": "作品甲", "staff": "总作画监督"}, {"id": 6, "type": 2, "name": "作品B", "staff": "原画"}])
    done = world.run(_finished(Who("chiefAnimationDirector", None, 41604)))
    assert [w["title"] for w in done["works"]] == ["作品甲"]


def test_a_voice_actor_lists_roles_with_characters_merged_per_anime():
    world = World(
        pages=[
            _voice_page(
                {"characterRole": "SUPPORTING", "characters": [{"name": {"native": "枢木スザク"}}], "node": _node(1, "コードギアス", 2006)},
                {"characterRole": "MAIN", "characters": [{"name": {"native": "ジノ"}}], "node": _node(1, "コードギアス", 2006)},
                {"characterRole": "SUPPORTING", "characters": [{"name": {"full": "Someone"}}], "node": _node(2, "作品B", 2010)},
            )
        ],
        # Bangumi's voice roles give the Chinese title of the anime.
        characters=[{"name": "枢木スザク", "staff": "主角", "subject_id": 8, "subject_type": 2, "subject_name": "コードギアス", "subject_name_cn": "反叛的鲁路修"}],
    )
    done = world.run(_finished(Who("cast", 95079, 4015)))
    first, second = done["works"]
    assert (second["title"], second["characters"], second["main"], second["bangumiId"]) == ("反叛的鲁路修", ["枢木スザク", "ジノ"], True, 8)
    assert (first["characters"], first["main"]) == (["Someone"], False)


def test_voice_actor_identity_is_found_by_exact_name_and_cached():
    world = World(
        tmdb={"name": "Takahiro Sakurai", "also_known_as": ["櫻井孝宏", "桜井孝宏"]},
        staff_search={
            "櫻井孝宏": [{"id": 1, "name": {"full": "Someone Else", "native": "櫻井太郎", "alternative": []}}],
            "Takahiro Sakurai": [{"id": 95079, "name": {"full": "Takahiro Sakurai", "native": "櫻井孝宏", "alternative": []}}],
        },
        person_search={"櫻井孝宏": [{"id": 4015, "name": "櫻井孝宏"}]},
    )

    async def scenario():
        first = await credits.cast_identity(1234, "櫻井孝宏")
        second = await credits.cast_identity(1234, "櫻井孝宏")
        return first, second

    first, second = world.run(scenario())
    assert first == second == (95079, 4015)
    # The second look came from the cache.
    assert world.tmdb.await_count == 1


def test_titles_match_despite_width_numerals_and_punctuation():
    assert credits._norm("チ。-地球の運動について-") == credits._norm("チ。 ―地球の運動について―")
    assert credits._norm("オーバーロードⅣ") == credits._norm("オーバーロードIV")
    assert credits._same_title("ダイヤのA[エース]", "ダイヤのA") is True
    assert credits._same_title("A", "AB") is False
