"""DotaHub: STRATZ matches as rows, who gets a DM, and one background check end to end."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.plugin.module_bot.dota import service, stratz
from app.plugin.module_bot.dota.model import DotaMatchModel, DotaProfileModel


def _match(match_id, start, *, parsed=True, deaths=2):
    return {
        "id": match_id,
        "firstBloodTime": 100,
        "startDateTime": int(start.replace(tzinfo=UTC).timestamp()),
        "durationSeconds": 2400,
        "parsedDateTime": int(start.replace(tzinfo=UTC).timestamp()) + 600 if parsed else None,
        "players": [
            {
                "heroId": 4,
                "isVictory": True,
                "kills": 5,
                "deaths": deaths,
                "assists": 7,
                "imp": 12,
                "position": "POSITION_2",
                "lane": "MID_LANE",
                "award": "MVP",
                "goldPerMinute": 600,
                "experiencePerMinute": 700,
                "networth": 20000,
                "numLastHits": 250,
                "numDenies": 10,
                "heroDamage": 30000,
                "stats": {
                    "networthPerMinute": [600, 900, 1300],
                    "impPerMinute": [0, 5, 12],
                    "lastHitsPerMinute": [0, 4, 6],
                    "itemPurchases": [{"time": -60, "itemId": 44}, {"time": 30, "itemId": 44}, {"time": 900, "itemId": 116}],
                    "deathEvents": [{"time": 100 * (i + 1), "positionX": 120, "positionY": 90, "attacker": 26, "goldLost": 50, "timeDead": 20} for i in range(deaths)],
                }
                if parsed
                else None,
            }
        ],
    }


def test_a_stratz_match_becomes_a_row():
    start = datetime(2026, 10, 19, 10, 0)
    row = stratz.to_row(1, _match(9, start))
    assert (row["match_id"], row["hero_id"], row["won"], row["imp"], row["parsed"]) == (9, 4, True, 12, True)
    assert row["death_events"][0] == {"t": 100, "x": 120, "y": 90, "by": 26, "gold": 50, "dead": 20, "fb": True}
    assert row["start_time"] == start
    assert stratz.to_row(1, {"id": 9, "startDateTime": 1, "players": []}) is None
    unparsed = stratz.to_row(1, _match(10, start, parsed=False))
    assert unparsed["parsed"] is False and unparsed["death_events"] == []


def test_a_parsed_match_keeps_its_economy_and_imp():
    row = stratz.to_row(1, _match(9, datetime(2026, 10, 19, 10, 0)))
    d = row["details"]
    assert (d["pos"], d["lane"], d["award"], d["gpm"], d["lh"]) == (2, "MID_LANE", "MVP", 600, 250)
    assert d["nwm"] == [600, 900, 1300] and d["impm"] == [0, 5, 12] and d["lhm"] == [0, 4, 6]
    # Only each item's first purchase, in time order.
    assert d["items"] == [[-60, 44], [900, 116]]
    assert stratz.to_row(1, _match(10, datetime(2026, 10, 19, 10, 0), parsed=False))["details"] is None


def test_the_first_blood_death_is_marked():
    row = stratz.to_row(1, _match(9, datetime(2026, 10, 19, 10, 0), deaths=3))
    assert [e["fb"] for e in row["death_events"]] == [True, False, False]
    row = stratz.to_row(1, {**_match(9, datetime(2026, 10, 19, 10, 0)), "firstBloodTime": None})
    assert not any(e["fb"] for e in row["death_events"])


def test_slow_matches_are_sent_back_to_the_parser_a_few_times():
    now = datetime(2026, 10, 19, 12, 0)
    service._reparsed.clear()
    assert not service.wants_reparse(1, now - timedelta(minutes=10), now)
    assert service.wants_reparse(1, now - timedelta(minutes=40), now)
    service._reparsed[1] = (1, now)
    assert not service.wants_reparse(1, now - timedelta(minutes=40), now + timedelta(hours=1))
    assert service.wants_reparse(1, now - timedelta(minutes=40), now + timedelta(hours=3))
    service._reparsed[1] = (3, now)
    assert not service.wants_reparse(1, now - timedelta(minutes=40), now + timedelta(days=1))
    service._reparsed.clear()


def test_who_gets_a_dm():
    now = datetime(2026, 10, 19, 12, 0)
    profile = SimpleNamespace(notify=True, discord_id="123456789012345678", linked_at=now - timedelta(days=1))
    row = {"parsed": True, "start_time": now - timedelta(hours=1)}
    assert service.should_send(profile, row, now)
    assert not service.should_send(SimpleNamespace(**{**vars(profile), "notify": False}), row, now)
    assert not service.should_send(SimpleNamespace(**{**vars(profile), "discord_id": None}), row, now)
    # Played well before linking (a game that ended just before is still sent), or too long ago: no DM.
    assert service.should_send(SimpleNamespace(**{**vars(profile), "linked_at": now}), row, now)
    assert not service.should_send(SimpleNamespace(**{**vars(profile), "linked_at": now + timedelta(hours=2)}), row, now)
    assert not service.should_send(profile, {**row, "start_time": now - timedelta(hours=13)}, now)
    assert not service.should_send(profile, {**row, "parsed": False}, now)


def test_a_check_queues_new_matches_and_stores_history(monkeypatch):
    now = datetime(2026, 10, 19, 12, 0)
    monkeypatch.setattr(service, "utcnow", lambda: now)
    recent = [_match(30, now - timedelta(minutes=50)), _match(29, now - timedelta(minutes=10), parsed=False), _match(28, now - timedelta(days=2), parsed=False)]
    history = [_match(30, now - timedelta(minutes=50)), _match(20, now - timedelta(days=3)), _match(21, now - timedelta(days=4), deaths=0)]

    async def ranked_matches(db, account_id, take, skip=0):
        return recent if take == service.RECENT else history[skip : skip + take]

    monkeypatch.setattr(stratz, "ranked_matches", ranked_matches)
    asked = []

    async def retry_parse(db, match_id):
        asked.append(match_id)
        return True

    monkeypatch.setattr(stratz, "retry_parse", retry_parse)
    service._reparsed.clear()

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            for model in (DotaProfileModel, DotaMatchModel):
                await conn.run_sync(model.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db, db.begin():
            profile = await service.link(db, "site-a", "76561198304403520", 344137792, "123456789012345678")
            profile.linked_at = now - timedelta(hours=2)
            await service.check(db, profile)
            rows = {m.match_id: m for m in await db.scalars(select(DotaMatchModel))}
            pending = await service.pending(db)
        await engine.dispose()
        return profile, rows, pending

    profile, rows, pending = asyncio.run(run())
    # The fresh parsed match is queued; the fresh unparsed one waits; the old unparsed one is kept
    # without positions; history is stored without DMs.
    assert rows[30].notify_state == "pending"
    assert 29 not in rows
    assert rows[28].parsed is False and rows[28].notify_state == "none"
    assert rows[20].notify_state == "none" and rows[21].deaths == 0
    assert [p["matchId"] for p in pending] == [30]
    assert profile.backfill_done and profile.backfill_skip == 3
    assert profile.next_check_at == now + service.CHECK
