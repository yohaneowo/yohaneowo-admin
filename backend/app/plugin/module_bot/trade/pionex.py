"""Pionex's own REST API (ccxt doesn't have it): HMAC-SHA256 over "METHOD/path?sorted query", the
key and the signature in headers, a `timestamp` within 20 s. Three accounts matter here: the
trading account (/api/v1/account/balances: manual spot), the futures account (/uapi/v1: manual
contracts) and the bot account, where grid bots keep their money and positions — the bot account is
only visible through the bot-order list and the balances overview (/api/v1/wallet/balancesFull),
and its fills aren't exposed at all. Limits: 10 requests a second per IP and per key (HTTP 429 and
a 60 s ban when exceeded)."""

import hashlib
import hmac
import time

import httpx

from .common import KeyRejected, SourceUnavailable, dec

BASE_URL = "https://api.pionex.com"
TIMEOUT = 20
# Spot fills come 100 at a time (no limit parameter), futures fills up to 200.
SPOT_PAGE = 100
USDM_PAGE = 200
# Bot-order pages are read at most this many at a time.
BOT_PAGES = 5
# Error codes that mean the key itself is the problem.
KEY_ERRORS = {"INVALID_APIKEY", "INVALID_SIGNATURE", "PERMISSION_DENIED", "API_KEY_NOT_FOUND", "INVALID_API_KEY"}
# Statuses of the bot-order list, as the API actually wants them (not as documented): "RUNNING" in
# upper case ("running" answers nothing), "finished" in lower case ("FINISHED" answers every bot,
# running ones included — the flow filters on closeTime to be safe).
RUNNING, FINISHED = "RUNNING", "finished"

_symbols: dict[str, tuple[float, list[dict]]] = {}
SYMBOLS_TTL = 6 * 3600


def perp_symbol(base: str, quote: str) -> str:
    """The futures symbol of a bot's base ("BTC.PERP") and quote: BTC_USDT_PERP."""
    return f"{base.removesuffix('.PERP')}_{quote}_PERP"


class Pionex:
    def __init__(self, api_key: str, secret: str) -> None:
        self.key = api_key
        self.secret = secret.encode()
        self.http = httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT)

    async def close(self) -> None:
        await self.http.aclose()

    async def restrictions(self) -> dict:
        """Pionex has no endpoint for a key's permissions: reading is proven by the balances, bot
        reading by the bot list. Withdrawals aren't a key permission there (transfers are) and can't
        be told; the website says so."""
        await self.spot_balances()
        try:
            await self.bots(RUNNING, pages=1)
            bot_reading = True
        except KeyRejected:
            bot_reading = False
        return {"reading": True, "trading": False, "withdrawals": False, "ipRestrict": False, "botReading": bot_reading}

    # ── Balances ──────────────────────────────────────────────────────────────────────────────

    async def spot_balances(self) -> list[dict]:
        """[{asset, free, locked}] of the trading account (manual spot), non-zero only."""
        data = await self._get("/api/v1/account/balances")
        out = []
        for b in data.get("balances", []):
            free, frozen = dec(b.get("free")), dec(b.get("frozen"))
            if free or frozen:
                out.append({"asset": b["coin"], "free": free, "locked": frozen})
        return out

    async def overview(self) -> dict:
        """The balances overview of every account (GET /api/v1/wallet/balancesFull): the bot
        account's bots (investment and profit each), its spot coins and earn, the futures account."""
        return await self._get("/api/v1/wallet/balancesFull")

    async def usdm_balances(self) -> list[dict]:
        data = await self._get("/uapi/v1/account/balances")
        out = []
        for b in data.get("balances", []):
            free, frozen = dec(b.get("free")), dec(b.get("frozen"))
            if free or frozen:
                out.append({"asset": b["coin"], "free": free, "locked": frozen})
        return out

    async def usdm_positions(self) -> list[dict]:
        data = await self._get("/uapi/v1/account/positions")
        return [p for p in data.get("positions", []) if dec(p.get("netSize")) or dec(p.get("sizeLong")) or dec(p.get("sizeShort"))]

    # ── Bots ──────────────────────────────────────────────────────────────────────────────────

    async def bots(self, status: str, pages: int = BOT_PAGES) -> list[dict]:
        """Bot orders of a status (RUNNING / FINISHED), newest first, up to `pages` pages."""
        out: list[dict] = []
        token = None
        for _ in range(pages):
            params = {"status": status}
            if token:
                params["pageToken"] = token
            data = await self._get("/api/v1/bot/orders", **params)
            out.extend(data.get("results") or [])
            token = data.get("nextPageToken")
            if not token:
                break
        return out

    # ── Markets and fills ─────────────────────────────────────────────────────────────────────

    async def symbols(self, kind: str) -> list[dict]:
        """The SPOT or PERP symbols (public, cached six hours)."""
        cached = _symbols.get(kind)
        if cached and time.monotonic() - cached[0] < SYMBOLS_TTL:
            return cached[1]
        data = await self._get("/api/v1/common/symbols", signed=False, type=kind)
        rows = data.get("symbols", [])
        _symbols[kind] = (time.monotonic(), rows)
        return rows

    async def spot_fills(self, symbol: str, start_ms: int | None, end_ms: int | None) -> list[dict]:
        """The latest 100 fills of a spot symbol in the window, newest first (GET /api/v1/trade/fills)."""
        params = {"symbol": symbol}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        return (await self._get("/api/v1/trade/fills", **params)).get("fills") or []

    async def usdm_fills(self, symbol: str, start_ms: int | None, end_ms: int | None) -> list[dict]:
        """The latest 200 fills of a futures symbol in the window, newest first (GET /uapi/v1/trade/fills)."""
        params = {"symbol": symbol, "limit": USDM_PAGE}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        return (await self._get("/uapi/v1/trade/fills", **params)).get("fills") or []

    async def _get(self, path: str, signed: bool = True, **params) -> dict:
        headers = {}
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            query = "&".join(f"{k}={params[k]}" for k in sorted(params))
            signature = hmac.new(self.secret, f"GET{path}?{query}".encode(), hashlib.sha256).hexdigest()
            headers = {"PIONEX-KEY": self.key, "PIONEX-SIGNATURE": signature}
        try:
            response = await self.http.get(path, params=params, headers=headers)
        except httpx.HTTPError as error:
            raise SourceUnavailable(f"Pionex 连不上：{error.__class__.__name__}") from error
        if response.status_code == 429:
            raise SourceUnavailable("Pionex 限流（每秒 10 次）")
        try:
            payload = response.json()
        except ValueError as error:
            raise SourceUnavailable(f"Pionex 回应不是 JSON（HTTP {response.status_code}）") from error
        if not payload.get("result"):
            code = str(payload.get("code") or f"HTTP {response.status_code}")
            message = str(payload.get("message") or "")[:200]
            if code in KEY_ERRORS or response.status_code == 401:
                raise KeyRejected(f"{code} {message}".strip())
            raise SourceUnavailable(f"Pionex 错误：{code} {message}".strip())
        return payload.get("data") or {}
