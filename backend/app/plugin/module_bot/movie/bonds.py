"""羁绊: how much two profiles have watched in common, from their statistics snapshots. Movies and
shows count per title; anime counts per season. Trakt's watched list no longer carries per-season
detail, so a show's watched seasons are estimated from its episode count: seasons are filled in
order with the episodes watched, and a season that got at least one episode counts as watched."""

from typing import Any

from .model import MovieSubscriberModel
from .sync.common import is_integer

KINDS = ("movies", "shows", "animeSeasons")


def _ids(items: list[dict] | None) -> set[int]:
    return {item["id"] for item in items or [] if is_integer(item.get("id"))}


def watched_seasons(item: dict) -> set[tuple[int, int]]:
    """(show id, season number) for the seasons this item's episodes reach into."""
    show_id = item.get("id")
    if not is_integer(show_id):
        return set()
    left = item.get("episodesWatched") if is_integer(item.get("episodesWatched")) else 0
    seasons = sorted((s for s in item.get("seasons") or [] if is_integer(s.get("number")) and s["number"] >= 1), key=lambda s: s["number"])
    watched: set[tuple[int, int]] = set()
    for season in seasons:
        episodes = season.get("episodes") if is_integer(season.get("episodes")) else 0
        if left <= 0:
            break
        if episodes <= 0:
            continue
        watched.add((show_id, season["number"]))
        left -= episodes
    if not watched and left > 0 and seasons:
        # Episodes watched but no season has an episode count: credit the first season.
        watched.add((show_id, seasons[0]["number"]))
    return watched


def units(snapshot: dict | None) -> dict[str, set]:
    """Everything a profile has watched, per kind."""
    snapshot = snapshot or {}
    anime = (snapshot.get("anime") or {}).get("items") or []
    return {
        "movies": _ids(snapshot.get("films")),
        "shows": _ids((snapshot.get("shows") or {}).get("items")),
        "animeSeasons": set().union(*(watched_seasons(item) for item in anime)) if anime else set(),
    }


def overlap(mine: dict[str, set], theirs: dict[str, set]) -> dict[str, Any]:
    """Common counts per kind and in total; the share is of what I have watched."""
    out: dict[str, Any] = {}
    common_total = mine_total = 0
    for kind in KINDS:
        common = len(mine[kind] & theirs[kind])
        out[kind] = {"common": common, "mine": len(mine[kind]), "theirs": len(theirs[kind])}
        common_total += common
        mine_total += len(mine[kind])
    out["total"] = {"common": common_total, "mine": mine_total, "share": round(common_total / mine_total, 4) if mine_total else 0.0}
    return out


# 口味相似度: over the titles both have rated, 100% when the ratings match and 0% once they are
# TASTE_SPREAD points apart on average; too few shared ratings say nothing, so it stays null.
TASTE_SPREAD = 4
TASTE_MIN_PAIRS = 5


def ratings(snapshot: dict | None) -> dict[tuple[str, int], float]:
    """(kind, id) → the profile's own rating, for every rated movie, show and anime title."""
    snapshot = snapshot or {}
    lists = {
        "movie": snapshot.get("films"),
        "show": (snapshot.get("shows") or {}).get("items"),
        "anime": (snapshot.get("anime") or {}).get("items"),
    }
    out: dict[tuple[str, int], float] = {}
    for kind, items in lists.items():
        for item in items or []:
            rating = item.get("myRating")
            if is_integer(item.get("id")) and isinstance(rating, (int, float)) and not isinstance(rating, bool):
                out[(kind, item["id"])] = float(rating)
    return out


def taste(mine: dict[tuple[str, int], float], theirs: dict[tuple[str, int], float]) -> dict[str, Any]:
    pairs = [(mine[key], theirs[key]) for key in mine.keys() & theirs.keys()]
    if len(pairs) < TASTE_MIN_PAIRS:
        return {"pairs": len(pairs), "similarity": None}
    gap = sum(abs(a - b) for a, b in pairs) / len(pairs)
    return {"pairs": len(pairs), "similarity": round(max(0.0, 1 - gap / TASTE_SPREAD), 4)}


def bonds(me: MovieSubscriberModel, others: list[MovieSubscriberModel]) -> list[dict]:
    """One row per other profile, most in common first."""
    mine = units(me.snapshot)
    my_ratings = ratings(me.snapshot)
    rows = []
    for other in others:
        if other.profile_id == me.profile_id or not other.discord_id:
            continue
        rows.append(
            {
                "discordId": other.discord_id,
                "traktUser": other.trakt_user,
                "traktName": other.trakt_name,
                **overlap(mine, units(other.snapshot)),
                "taste": taste(my_ratings, ratings(other.snapshot)),
            }
        )
    return sorted(rows, key=lambda r: (-r["total"]["common"], r["traktUser"]))
