"""Bybit (v5 API) through ccxt's raw endpoints, like binance.py. Only Unified Trading Accounts: the
wallet is one UNIFIED account (spot, USDⓈ-M and inverse contracts share it), the funding account and
Earn are separate. Fills come as one stream per product category (no symbol needed), 7-day windows,
2 years back."""

from decimal import Decimal

import ccxt.async_support as ccxt
from ccxt.base.errors import AuthenticationError, DDoSProtection, PermissionDenied, RateLimitExceeded

from .common import KeyRejected, SourceUnavailable, dec

PAGE = 100
# Executions are kept two years; the first window starts a little inside that (a start exactly two
# years back is refused: "Can't query order earlier than 2 years").
HISTORY_MS = (2 * 365 - 3) * 24 * 3600 * 1000
WINDOW_MS = 7 * 24 * 3600 * 1000
# category → product: USDT / USDC-settled contracts are usdm, coin-settled are coinm.
CATEGORIES = {"spot": "spot", "linear": "usdm", "inverse": "coinm"}
EARN_CATEGORIES = ("FlexibleSaving", "OnChain")


class Bybit:
    def __init__(self, api_key: str, secret: str) -> None:
        self.api = ccxt.bybit({"apiKey": api_key, "secret": secret, "enableRateLimit": True, "options": {"adjustForTimeDifference": True, "recvWindow": 10_000}})
        self._markets: dict | None = None

    async def close(self) -> None:
        await self.api.close()

    async def restrictions(self) -> dict:
        """What the key may do (GET /v5/user/query-api). A key that isn't read-only is "trading";
        `unified` says whether the account is a UTA (the only kind read here)."""
        r = (await self._call(self.api.private_get_v5_user_query_api))["result"]
        permissions = r.get("permissions") or {}
        return {
            "reading": True,
            "trading": str(r.get("readOnly")) != "1",
            "withdrawals": "Withdraw" in (permissions.get("Wallet") or []),
            "ipRestrict": bool(r.get("ips")),
            "unified": str(r.get("uta")) == "1",
        }

    # ── Balances ──────────────────────────────────────────────────────────────────────────────

    async def prices(self) -> dict[str, Decimal]:
        """Every coin's USDT price from the spot tickers (public), USDT itself at 1."""
        r = (await self._call(self.api.public_get_v5_market_tickers, {"category": "spot"}))["result"]
        out = {"USDT": Decimal(1)}
        for t in r.get("list", []):
            symbol = t.get("symbol") or ""
            if symbol.endswith("USDT") and t.get("lastPrice"):
                out.setdefault(symbol[:-4], dec(t["lastPrice"]))
        return out

    async def unified_balances(self) -> list[dict]:
        """[{asset, free, locked, total, unrealized, price_usd}] of the unified account, non-zero
        only; the total is the wallet balance, the unrealized profit of its contracts beside it, the
        price what Bybit values the coin at."""
        r = (await self._call(self.api.private_get_v5_account_wallet_balance, {"accountType": "UNIFIED"}))["result"]
        out = []
        for account in r.get("list", []):
            for c in account.get("coin", []):
                total, unrealized = dec(c.get("walletBalance")), dec(c.get("unrealisedPnl"))
                if not total and not unrealized:
                    continue
                locked = dec(c.get("locked"))
                usd = dec(c.get("usdValue"))
                price = (usd / total) if total and usd else None
                out.append({"asset": c["coin"], "free": max(total - locked, dec(0)), "locked": locked, "total": total, "unrealized": unrealized, "price_usd": price})
        return out

    async def fund_balances(self) -> list[dict]:
        r = (await self._call(self.api.private_get_v5_asset_transfer_query_account_coins_balance, {"accountType": "FUND"}))["result"]
        out = []
        for c in r.get("balance", []):
            total = dec(c.get("walletBalance"))
            if not total:
                continue
            free = dec(c.get("transferBalance"))
            out.append({"asset": c["coin"], "free": free, "locked": max(total - free, dec(0))})
        return out

    async def overview(self) -> list[dict]:
        """Every account's equity per coin (GET /v5/asset/asset-overview): [{accountType,
        totalEquity (USD), coins: {coin: equity}}]. The only place the trading bots (grids), copy
        trading, Launchpool and the like show up; the unified and funding accounts are read in
        more detail elsewhere."""
        r = (await self._call(self.api.private_get_v5_asset_asset_overview, {}))["result"]
        out = []
        for a in r.get("list", []):
            coins: dict[str, Decimal] = {}
            details = list(a.get("coinDetail") or [])
            for category in a.get("categories") or []:
                details.extend(category.get("coinDetail") or [])
            for c in details:
                amount = dec(c.get("equity"))
                if amount:
                    coins[c["coin"]] = coins.get(c["coin"], Decimal(0)) + amount
            out.append({"accountType": a.get("accountType") or "", "totalEquity": dec(a.get("totalEquity")), "coins": coins})
        return out

    async def earn_balances(self) -> list[dict]:
        """Earn positions (flexible savings, on-chain staking) summed per coin, all locked."""
        totals: dict = {}
        for category in EARN_CATEGORIES:
            try:
                r = (await self._call(self.api.private_get_v5_earn_position, {"category": category}))["result"]
            except KeyRejected:
                # Keys without the Earn permission are refused here; the rest still works.
                continue
            for p in r.get("list", []):
                amount = dec(p.get("amount"))
                if amount:
                    totals[p["coin"]] = totals.get(p["coin"], dec(0)) + amount
        return [{"asset": asset, "free": dec(0), "locked": total} for asset, total in totals.items()]

    async def positions(self) -> list[dict]:
        """Open contract positions, each with "_product" (usdm / coinm) added."""
        out = []
        for category, params in (("linear", {"settleCoin": "USDT"}), ("linear", {"settleCoin": "USDC"}), ("inverse", {})):
            cursor = None
            while True:
                r = (await self._call(self.api.private_get_v5_position_list, {"category": category, "limit": 200, **params, **({"cursor": cursor} if cursor else {})}))["result"]
                out.extend({**p, "_product": CATEGORIES[category]} for p in r.get("list", []) if dec(p.get("size")))
                cursor = r.get("nextPageCursor")
                if not cursor:
                    break
        return out

    # ── Markets and fills ─────────────────────────────────────────────────────────────────────

    async def markets(self) -> dict:
        if self._markets is None:
            self._markets = await self._call(self.api.load_markets)
        return self._markets

    async def executions(self, category: str, start_ms: int, end_ms: int, cursor: str | None) -> tuple[list[dict], str | None]:
        """A page of fills of a category in [start_ms, end_ms] (at most 7 days), newest first, and
        the cursor of the next page (GET /v5/execution/list)."""
        params = {"category": category, "startTime": start_ms, "endTime": end_ms, "limit": PAGE}
        if cursor:
            params["cursor"] = cursor
        r = (await self._call(self.api.private_get_v5_execution_list, params))["result"]
        return r.get("list", []), r.get("nextPageCursor") or None

    async def _call(self, method, *args):
        try:
            return await method(*args)
        except (AuthenticationError, PermissionDenied) as error:
            raise KeyRejected(str(error)[:300]) from error
        except (RateLimitExceeded, DDoSProtection) as error:
            raise SourceUnavailable(f"Bybit 限流：{str(error)[:200]}") from error
        except ccxt.BaseError as error:
            raise SourceUnavailable(f"Bybit 错误：{str(error)[:300]}") from error
