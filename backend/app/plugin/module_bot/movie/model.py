from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


class MovieSubscriberModel(MappedBase):
    __tablename__ = "bot_movie_subscriber"

    discord_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    trakt_user: Mapped[str] = mapped_column(String(255))
    trakt_name: Mapped[str] = mapped_column(String(255))
    region: Mapped[str] = mapped_column(String(2))
    subscribed_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[str] = mapped_column(String(36))
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Copied from the snapshot so the platform-wide rank is one indexed count, not a JSON scan.
    watched_count: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    shows_watched_count: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    anime_watched_count: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # The taste profile the bot's recommendations read (genres, people, seen and rated movies);
    # written by the same sync as the snapshot, but kept apart so the website never downloads it.
    taste: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sync_requested: Mapped[bool] = mapped_column(Boolean, default=True)
    lease_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MovieRecordModel(MappedBase):
    __tablename__ = "bot_movie_record"

    discord_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class MovieCacheModel(MappedBase):
    __tablename__ = "bot_movie_cache"

    namespace: Mapped[str] = mapped_column(String(32), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
