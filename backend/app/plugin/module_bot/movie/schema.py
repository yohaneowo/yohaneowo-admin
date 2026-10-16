from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.validator import DateTimeStr

DiscordId = Annotated[str, Field(pattern=r"^[0-9]{15,22}$")]
# The website's account id (Better Auth); never shown to anyone, only used as a key.
SiteUserId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class SubscriptionIn(BaseModel):
    """A profile's Trakt details as the bot's dev import sends them (already resolved)."""

    model_config = ConfigDict(extra="forbid")
    traktUser: str = Field(min_length=1, max_length=255, pattern=r"^[^/\\\\?#\s]+$")
    traktName: str = Field(min_length=1, max_length=255)
    region: str = Field(pattern=r"^[A-Z]{2}$")
    subscribedAt: datetime | None = None


class ProfileIn(BaseModel):
    """What a person types on the website: a Trakt profile link or username, and where they watch."""

    model_config = ConfigDict(extra="forbid")
    trakt: str = Field(min_length=1, max_length=300)
    region: str = Field(pattern=r"^[A-Z]{2}$")
    # The website account's linked Discord user, so the bot can find this profile.
    discordId: DiscordId | None = None


class SubscriptionOut(BaseModel):
    profileId: str
    # Discord user id ("userId" is what the bot has always called it); None until Discord is linked.
    userId: str | None
    siteUserId: str | None
    traktUser: str
    traktName: str
    region: str
    subscribedAt: DateTimeStr
    active: bool
    recommendations: bool
    revision: str
    syncedAt: DateTimeStr | None
    syncError: str | None
    syncRequested: bool
    syncing: bool


class Film(BaseModel):
    id: int
    title: str = Field(max_length=500)
    rating: float = Field(ge=1, le=10)


class Person(BaseModel):
    id: int
    name: str = Field(max_length=255)
    # The person's own name for the English interface (TMDB original_name; AniList for anime staff).
    nameEn: str | None = Field(default=None, max_length=255)
    role: Literal["director", "cast"]
    photo: str | None = Field(default=None, pattern=r"^https://image\.tmdb\.org/t/p/w185/[A-Za-z0-9._-]+$")
    average: float = Field(ge=1, le=10)
    count: int = Field(ge=2)
    films: list[Film] = Field(max_length=3)


class Genre(BaseModel):
    id: int
    name: str = Field(max_length=100)
    count: int = Field(ge=0)
    ratedCount: int = Field(ge=0)
    interest: float = Field(ge=0, le=1)
    affinity: float | None
    average: float | None


class ProfileFilm(BaseModel):
    id: int
    title: str = Field(max_length=500)
    year: int | None = Field(default=None, ge=1880, le=2100)
    releaseDate: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    director: str | None = Field(default=None, max_length=255)
    directorId: int | None = Field(default=None, ge=1)
    directorEn: str | None = Field(default=None, max_length=255)
    genre: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    publicScore: float | None = Field(default=None, ge=0, le=10)
    voteCount: int | None = Field(default=None, ge=0)
    genres: list[Annotated[str, Field(max_length=100)]] = Field(default_factory=list, max_length=20)
    imdbId: str | None = Field(default=None, pattern=r"^tt\d{5,12}$")
    imdbRating: float | None = Field(default=None, ge=0, le=10)
    imdbVotes: int | None = Field(default=None, ge=0)
    rottenTomatoes: int | None = Field(default=None, ge=0, le=100)
    metacritic: int | None = Field(default=None, ge=0, le=100)
    myRating: float | None = Field(default=None, ge=1, le=10)
    watchedAt: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    poster: str | None = Field(default=None, pattern=r"^https://image\.tmdb\.org/t/p/w185/[A-Za-z0-9._-]+$")
    # Trakt tags it "anime" (the website can show or hide anime films).
    anime: bool = False
    # Plays (episode plays for a show); 0 when only rated. Lets the website count within its filters.
    plays: int | None = Field(default=None, ge=0)
    originalTitle: str | None = Field(default=None, max_length=500)
    titleEn: str | None = Field(default=None, max_length=500)


class ShowSeason(BaseModel):
    number: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=255)
    episodes: int | None = Field(default=None, ge=0)
    airDate: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    publicScore: float | None = Field(default=None, ge=0, le=10)
    myRating: float | None = Field(default=None, ge=1, le=10)
    # Anime only: the season's own Bangumi / AniList / MyAnimeList entry.
    titleZh: str | None = Field(default=None, max_length=500)
    titleEn: str | None = Field(default=None, max_length=500)
    bangumiId: int | None = Field(default=None, ge=1)
    bangumiScore: float | None = Field(default=None, ge=0, le=10)
    anilistId: int | None = Field(default=None, ge=1)
    malId: int | None = Field(default=None, ge=1)
    malScore: float | None = Field(default=None, ge=0, le=10)


ANIME_ROLES = Literal["director", "original", "studio", "music", "chiefAnimationDirector"]


class AnimeStaff(BaseModel):
    role: ANIME_ROLES
    # AniList's ID; negative for people only Bangumi lists (minus Bangumi's ID).
    id: int
    name: str = Field(max_length=255)
    nameEn: str | None = Field(default=None, max_length=255)


class ShowItem(ProfileFilm):
    """A show row: ProfileFilm's fields (releaseDate = first air date, director = creator) plus progress."""

    episodesWatched: int = Field(default=0, ge=0)
    episodesAired: int | None = Field(default=None, ge=0)
    seasons: list[ShowSeason] = Field(default_factory=list, max_length=200)
    # Anime only: the series' first season on Bangumi / MyAnimeList, and its staff across seasons.
    bangumiId: int | None = Field(default=None, ge=1)
    bangumiScore: float | None = Field(default=None, ge=0, le=10)
    bangumiVotes: int | None = Field(default=None, ge=0)
    anilistId: int | None = Field(default=None, ge=1)
    malId: int | None = Field(default=None, ge=1)
    malScore: float | None = Field(default=None, ge=0, le=10)
    malVotes: int | None = Field(default=None, ge=0)
    staff: list[AnimeStaff] = Field(default_factory=list, max_length=100)


class ShowPerson(Person):
    role: Literal["creator", "cast", "director", "original", "studio", "music", "chiefAnimationDirector"]


class ShowSection(BaseModel):
    watchedCount: int = Field(ge=0)
    episodes: int = Field(ge=0)
    ratingCount: int = Field(ge=0)
    mean: float | None = Field(default=None, ge=1, le=10)
    distribution: list[Annotated[int, Field(ge=0)]] = Field(min_length=10, max_length=10)
    genres: list[Genre] = Field(max_length=100)
    people: list[ShowPerson] = Field(max_length=56)
    items: list[ShowItem] = Field(default_factory=list, max_length=3000)


class Snapshot(BaseModel):
    version: Literal[1] = 1
    ratingCount: int = Field(ge=0)
    watchedCount: int = Field(ge=0)
    plays: int = Field(ge=0)
    mean: float | None = Field(default=None, ge=1, le=10)
    showRatingCount: int = Field(ge=0)
    showMean: float | None = Field(default=None, ge=1, le=10)
    distribution: list[Annotated[int, Field(ge=0)]] = Field(min_length=10, max_length=10)
    sampleCount: int = Field(ge=0, le=200)
    sampleLimit: Literal[200] = 200
    genres: list[Genre] = Field(max_length=100)
    people: list[Person] = Field(max_length=16)
    films: list[ProfileFilm] = Field(default_factory=list, max_length=3000)
    # Every movie the user has rated (TMDB IDs), for the ✓ on a director's filmography.
    ratedIds: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list, max_length=20000)
    # Average share watched of each director (2+ films seen) and series the user has started.
    depth: float | None = Field(default=None, ge=0, le=1)
    depthGroups: int = Field(default=0, ge=0)
    # TV: watched or rated shows, anime series apart; None for snapshots from before they were synced.
    shows: ShowSection | None = None
    anime: ShowSection | None = None


class RecordPatch(BaseModel):
    data: dict
    revision: str | None = None


class CacheBatch(BaseModel):
    items: dict[str, dict] = Field(max_length=200)


class CacheKeys(BaseModel):
    keys: list[str] = Field(max_length=200)


class SourceRequest(BaseModel):
    """One GET the bot asks admin to make on its behalf (admin holds the keys and counts usage)."""

    path: str = Field(max_length=300)
    params: dict[str, str | int] = Field(default_factory=dict, max_length=20)


class WikimediaRequest(BaseModel):
    url: str = Field(max_length=2000)


class RatingsIn(BaseModel):
    imdbIds: list[Annotated[str, Field(pattern=r"^tt\d{5,12}$")]] = Field(max_length=200)
