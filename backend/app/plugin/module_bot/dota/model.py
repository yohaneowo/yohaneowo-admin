from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class DotaProfileModel(MappedBase):
    """A website account's linked Steam account (the website signs in with Steam and tells admin):
    whose ranked matches the background check reads from STRATZ, where to DM the death map, and how
    far the history has been filled in for the heat map."""

    __tablename__ = "bot_dota_profile"

    site_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    steam_id: Mapped[str] = mapped_column(String(20), unique=True)
    # Dota's 32-bit account id (SteamID64 minus 76561197960265728), what STRATZ and OpenDota use.
    account_id: Mapped[int] = mapped_column(BigInteger)
    discord_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # DM the death map after each ranked match (on by default; the account settings turn it off).
    notify: Mapped[bool] = mapped_column(Boolean, default=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The history: ranked matches read so far, newest first (STRATZ pages of 100).
    backfill_skip: Mapped[int] = mapped_column(Integer, default=0)
    backfill_done: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class DotaMatchModel(MappedBase):
    """One ranked match of a linked account: the result and where the player died (STRATZ's death
    events: seconds, map cell x/y, the killer hero, gold lost, seconds dead)."""

    __tablename__ = "bot_dota_match"
    __table_args__ = (
        Index("ix_bot_dota_match_account_start", "account_id", "start_time"),
        Index("ix_bot_dota_match_notify", "notify_state"),
    )

    account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    match_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    hero_id: Mapped[int] = mapped_column(Integer)
    won: Mapped[bool] = mapped_column(Boolean)
    start_time: Mapped[datetime] = mapped_column(DateTime)
    duration: Mapped[int] = mapped_column(Integer)
    kills: Mapped[int] = mapped_column(Integer)
    deaths: Mapped[int] = mapped_column(Integer)
    assists: Mapped[int] = mapped_column(Integer)
    imp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # False: STRATZ never parsed it, so the positions are unknown (kept for the counts).
    parsed: Mapped[bool] = mapped_column(Boolean, default=True)
    death_events: Mapped[list] = mapped_column(JSON, default=list)
    # Economy and IMP for the DotaHub pages (stratz.details); None until read with them.
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The other nine players (stratz.others_of), for comparing against the same bracket; None until read.
    others: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # none (history, or no DM wanted), pending (for the bot), sent.
    notify_state: Mapped[str] = mapped_column(String(16), default="none")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
