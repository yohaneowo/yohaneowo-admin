"""Binance through ccxt's raw ("implicit") endpoints: ccxt signs the requests, knows the hosts and
paces them, while the raw JSON keeps Binance's decimal strings (ccxt's unified methods would parse
them to floats). Spot (api/sapi) and COIN-M futures (dapi) are two clients on the same key."""

import logging
from decimal import Decimal

import ccxt.async_support as ccxt
from ccxt.base.errors import AuthenticationError, DDoSProtection, PermissionDenied, RateLimitExceeded

from ..movie.sources.base import SourceUnavailable

PAGE = 1000
# Quote assets a held asset is paired with when looking for its spot market, in order.
QUOTES = ("USDT", "USDC")
# Held assets whose spot market has another base symbol (Binance's tokenized stocks).
ASSET_BASES = {"EQ_MSTR": "MSTRB"}
# Never a base to look for fills of: quotes and other stablecoins.
STABLES = {"USDT", "USDC", "FDUSD", "BUSD", "TUSD", "DAI", "USDP", "USD1", "EUR"}


# ccxt logs every request and response body at DEBUG: account balances and signed URLs don't belong
# in the log files.
logging.getLogger("ccxt").setLevel(logging.WARNING)


class KeyRejected(Exception):
    """The key is invalid or lacks a permission; the connection should stop trying for a while."""


def dec(value) -> Decimal:
    return Decimal(str(value if value not in (None, "") else "0"))


class Binance:
    def __init__(self, api_key: str, secret: str) -> None:
        options = {"adjustForTimeDifference": True, "recvWindow": 10_000, "warnOnFetchOpenOrdersWithoutSymbol": False}
        self.spot = ccxt.binance({"apiKey": api_key, "secret": secret, "enableRateLimit": True, "options": {**options, "defaultType": "spot"}})
        self.coinm = ccxt.binance({"apiKey": api_key, "secret": secret, "enableRateLimit": True, "options": {**options, "defaultType": "delivery"}})
        self._markets: dict | None = None

    async def close(self) -> None:
        await self.spot.close()
        await self.coinm.close()

    async def restrictions(self) -> dict:
        """What the key may do (GET /sapi/v1/account/apiRestrictions)."""
        r = await self._call(self.spot.sapi_get_account_apirestrictions)
        return {
            "reading": bool(r.get("enableReading")),
            "trading": bool(r.get("enableSpotAndMarginTrading")),
            "withdrawals": bool(r.get("enableWithdrawals")),
            "futures": bool(r.get("enableFutures")),
            "ipRestrict": bool(r.get("ipRestrict")),
        }

    # ── Balances ──────────────────────────────────────────────────────────────────────────────

    async def spot_balances(self) -> list[dict]:
        """[{asset, free, locked}] of the spot wallet, non-zero only."""
        r = await self._call(self.spot.private_get_account)
        return [{"asset": b["asset"], "free": dec(b["free"]), "locked": dec(b["locked"])} for b in r.get("balances", []) if dec(b["free"]) or dec(b["locked"])]

    async def funding_balances(self) -> list[dict]:
        r = await self._call(self.spot.sapi_post_asset_get_funding_asset, {})
        out = []
        for b in r or []:
            locked = dec(b.get("locked")) + dec(b.get("freeze")) + dec(b.get("withdrawing"))
            if dec(b.get("free")) or locked:
                out.append({"asset": b["asset"], "free": dec(b.get("free")), "locked": locked})
        return out

    async def earn_balances(self) -> list[dict]:
        """Simple Earn flexible and locked positions, summed per asset (all locked: not spendable)."""
        totals: dict[str, Decimal] = {}
        for method, field in ((self.spot.sapi_get_simple_earn_flexible_position, "totalAmount"), (self.spot.sapi_get_simple_earn_locked_position, "amount")):
            page = 1
            while True:
                r = await self._call(method, {"current": page, "size": 100})
                rows = r.get("rows", []) if isinstance(r, dict) else []
                for row in rows:
                    amount = dec(row.get(field))
                    if amount:
                        totals[row["asset"]] = totals.get(row["asset"], Decimal(0)) + amount
                if len(rows) < 100 or page * 100 >= int(r.get("total", 0)):
                    break
                page += 1
        return [{"asset": asset, "free": Decimal(0), "locked": total} for asset, total in totals.items()]

    async def coinm_wallet(self) -> list[dict]:
        """[{asset, free, locked, total, unrealized}] of the COIN-M futures wallet."""
        r = await self._call(self.coinm.dapiprivate_get_account)
        out = []
        for a in r.get("assets", []):
            total = dec(a.get("walletBalance"))
            if not total and not dec(a.get("unrealizedProfit")):
                continue
            free = dec(a.get("availableBalance"))
            out.append({"asset": a["asset"], "free": free, "locked": max(total - free, Decimal(0)), "total": total, "unrealized": dec(a.get("unrealizedProfit"))})
        return out

    async def coinm_positions(self) -> list[dict]:
        r = await self._call(self.coinm.dapiprivate_get_positionrisk)
        return [p for p in r if dec(p.get("positionAmt"))]

    # ── Markets ───────────────────────────────────────────────────────────────────────────────

    async def markets(self) -> dict:
        if self._markets is None:
            self._markets = await self._call(self.spot.load_markets)
        return self._markets

    async def spot_symbols_for(self, assets: set[str]) -> list[str]:
        """The spot symbol ids to read fills of for these held assets: each asset against the first
        quotes it has an active market with, by the exchange's own base symbol."""
        markets = await self.markets()
        ids = {m["id"] for m in markets.values() if m.get("spot") and m.get("active") is not False}
        out = []
        for asset in sorted(assets):
            base = ASSET_BASES.get(asset, asset)
            if base in STABLES:
                continue
            out.extend(f"{base}{quote}" for quote in QUOTES if f"{base}{quote}" in ids)
        return out

    # ── Fills and income ──────────────────────────────────────────────────────────────────────

    async def spot_trades(self, symbol: str, from_id: int) -> list[dict]:
        """A page of fills with id ≥ from_id, oldest first (GET /api/v3/myTrades)."""
        return await self._call(self.spot.private_get_mytrades, {"symbol": symbol, "fromId": from_id, "limit": PAGE})

    async def coinm_trades(self, symbol: str, from_id: int) -> list[dict]:
        return await self._call(self.coinm.dapiprivate_get_usertrades, {"symbol": symbol, "fromId": from_id, "limit": PAGE})

    async def coinm_income(self, start_ms: int, end_ms: int) -> list[dict]:
        """A page of income records in [start_ms, end_ms], oldest first (GET /dapi/v1/income; the
        window may be at most 200 days)."""
        return await self._call(self.coinm.dapiprivate_get_income, {"startTime": start_ms, "endTime": end_ms, "limit": PAGE})

    async def _call(self, method, *args):
        try:
            return await method(*args)
        except (AuthenticationError, PermissionDenied) as error:
            raise KeyRejected(str(error)[:300]) from error
        except (RateLimitExceeded, DDoSProtection) as error:
            raise SourceUnavailable(f"Binance 限流：{str(error)[:200]}") from error
        except ccxt.BaseError as error:
            raise SourceUnavailable(f"Binance 错误：{str(error)[:300]}") from error
