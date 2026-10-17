from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


class MovieSubscriberModel(MappedBase):
    """A movie profile: one Trakt account plus the statistics snapshot, taste and recommendation
    state built from it. Owned by a website account (site_user_id) and/or a Discord user
    (discord_id); either may be missing, and the website is where the Trakt details are entered."""

    __tablename__ = "bot_movie_subscriber"

    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    discord_id: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    site_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    # Trakt profile slug; one profile per Trakt account.
    trakt_user: Mapped[str] = mapped_column(String(255), unique=True)
    trakt_name: Mapped[str] = mapped_column(String(255))
    trakt_avatar: Mapped[str | None] = mapped_column(String(500), nullable=True)
    trakt_private: Mapped[bool] = mapped_column(Boolean, default=False)
    # OAuth tokens (Fernet-encrypted, app/utils/crypto_util.py) from the website's "connect Trakt";
    # they let the sync read private accounts. trakt_reauth: the refresh failed, the person must
    # authorize again before the next sync can run.
    trakt_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    trakt_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    trakt_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    trakt_redirect_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    trakt_reauth: Mapped[bool] = mapped_column(Boolean, default=False)
    region: Mapped[str] = mapped_column(String(2))
    subscribed_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Daily recommendations by Discord DM (the bot's /movie subscribe); needs discord_id.
    recommendations: Mapped[bool] = mapped_column(Boolean, default=False)
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
    """The bot's recommendation history for a profile, one row per TMDB movie."""

    __tablename__ = "bot_movie_record"

    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class MovieCacheModel(MappedBase):
    __tablename__ = "bot_movie_cache"

    namespace: Mapped[str] = mapped_column(String(32), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
