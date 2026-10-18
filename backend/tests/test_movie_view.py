"""Viewing someone else's statistics: a read-only lookup that never claims or merges profiles."""

import asyncio
from types import SimpleNamespace

from app.plugin.module_bot.movie import service


def _run(monkeypatch, by_site, by_discord, site_user_id="site-a", discord_id="111"):
    async def fake_site(db, uid, *, lock=False):
        return by_site

    async def fake_discord(db, did, *, lock=False):
        return by_discord

    monkeypatch.setattr(service, "by_site_user", fake_site)
    monkeypatch.setattr(service, "by_discord", fake_discord)
    return asyncio.run(service.peek_profile(None, site_user_id, discord_id))


def test_prefers_the_website_profile(monkeypatch):
    site = SimpleNamespace(site_user_id="site-a", profile_id="p1")
    bot = SimpleNamespace(site_user_id=None, profile_id="p2")
    assert _run(monkeypatch, site, bot) is site
    # Nothing was merged or claimed.
    assert bot.site_user_id is None


def test_shows_an_unclaimed_bot_profile_without_claiming_it(monkeypatch):
    bot = SimpleNamespace(site_user_id=None, profile_id="p2")
    assert _run(monkeypatch, None, bot) is bot
    assert bot.site_user_id is None


def test_ignores_a_bot_profile_owned_by_another_account(monkeypatch):
    bot = SimpleNamespace(site_user_id="site-b", profile_id="p2")
    assert _run(monkeypatch, None, bot) is None


def test_no_discord_id_means_website_profile_only(monkeypatch):
    assert _run(monkeypatch, None, SimpleNamespace(site_user_id=None), discord_id=None) is None
