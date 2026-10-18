from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Index, Integer, String, Text
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
    # /movie recommend picks a day for this profile instead of the default (test accounts).
    pick_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # /anime recommend: its own daily picks (test accounts), and whether this season's airing anime
    # are among the picks (the website's account settings switch; on by default).
    anime_pick_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anime_airing: Mapped[bool] = mapped_column(Boolean, default=True)
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


class MoviePickModel(MappedBase):
    """One `/movie recommend` pick: what was shown to whom, and what they did with it (想看 →
    watchlist, 看过了 → watched + rating, 换一部 → skip). Counts the day's quota, keeps a film
    from being picked again for a month and feeds the mode weights."""

    __tablename__ = "bot_movie_pick"
    __table_args__ = (Index("ix_bot_movie_pick_profile_day", "profile_id", "day"), Index("ix_bot_movie_pick_profile_shown", "profile_id", "shown_at"))

    pick_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    profile_id: Mapped[str] = mapped_column(String(36))
    tmdb_id: Mapped[int] = mapped_column(Integer)
    # comfort / bridge / explore
    mode: Mapped[str] = mapped_column(String(16))
    # The Taipei calendar day the pick counts against.
    day: Mapped[str] = mapped_column(String(10))
    shown_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    title: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str] = mapped_column(String(500))
    # watchlist / watched / watched_existing / rated / skip, None while the person has not answered
    # (or took their rating back).
    action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The rating was written through this pick (so it may be removed again and refunds the pick);
    # history_id is the Trakt play 看过了 added, removed together with the rating.
    own_rating: Mapped[bool] = mapped_column(Boolean, default=False)
    history_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The card the bot showed (title, overview, poster…), so /movie status can list it.
    data: Mapped[dict] = mapped_column(JSON)


class AnimePickModel(MappedBase):
    """One `/anime recommend` pick (an AniList entry: a season, a film or an ONA), like
    MoviePickModel: the day's quota, the 30-day no-repeat rule, the pool weights and what was
    written to Trakt (想看 → watchlist, 看过了 → the season's plays + rating) so it can be undone."""

    __tablename__ = "bot_anime_pick"
    __table_args__ = (Index("ix_bot_anime_pick_profile_day", "profile_id", "day"), Index("ix_bot_anime_pick_profile_shown", "profile_id", "shown_at"))

    pick_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    profile_id: Mapped[str] = mapped_column(String(36))
    anilist_id: Mapped[int] = mapped_column(Integer)
    # sequel / comfort / bridge / explore / airing
    mode: Mapped[str] = mapped_column(String(16))
    day: Mapped[str] = mapped_column(String(10))
    shown_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    title: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str] = mapped_column(String(500))
    # watchlist / watched / watched_existing / skip, None while unanswered (or a rating taken back).
    action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    own_rating: Mapped[bool] = mapped_column(Boolean, default=False)
    # What 看过了 added: a film's play (its history id) or a season's plays (removed by season).
    history_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    history_added: Mapped[bool] = mapped_column(Boolean, default=False)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The card the bot showed, with the Trakt target ({type, tmdbId, season, traktId} or null).
    data: Mapped[dict] = mapped_column(JSON)


class MovieCacheModel(MappedBase):
    __tablename__ = "bot_movie_cache"

    namespace: Mapped[str] = mapped_column(String(32), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
