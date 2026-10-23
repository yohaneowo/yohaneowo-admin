"""What the exchange adapters (binance.py, bybit.py, pionex.py) and the service share: the errors,
the Decimal helpers and the time conversions. Amounts are Decimal end to end; times are naive UTC
datetimes in the database and milliseconds on the wire."""

import logging
from datetime import UTC, datetime
from decimal import Decimal

from ..movie.sources.base import SourceUnavailable

__all__ = ["EXCHANGES", "KeyRejected", "SourceUnavailable", "dec", "dt_to_ms", "ms_to_dt", "num", "utcnow"]

# The exchanges a key can be connected for; the website's connect form offers the same three.
EXCHANGES = ("binance", "bybit", "pionex")

# ccxt logs every request and response body at DEBUG: account balances and signed URLs don't belong
# in the log files.
logging.getLogger("ccxt").setLevel(logging.WARNING)


class KeyRejected(Exception):
    """The key is invalid or lacks a permission; the connection should stop trying for a while.
    Known reasons (the website maps them to messages): withdrawals, reading, classic (Bybit account
    isn't unified), unknownSymbol; anything else shows as "key invalid"."""


def dec(value) -> Decimal:
    return Decimal(str(value if value not in (None, "") else "0"))


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def ms_to_dt(ms) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).replace(tzinfo=None)


def dt_to_ms(dt: datetime | None) -> int | None:
    return None if dt is None else int(dt.replace(tzinfo=UTC).timestamp() * 1000)


def num(value: Decimal | None) -> str | None:
    """A decimal as the website shows it: no exponent, no trailing zeros."""
    if value is None:
        return None
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
