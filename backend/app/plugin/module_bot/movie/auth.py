import hashlib
import hmac
from functools import lru_cache
from typing import Annotated

from fastapi import Header, HTTPException
from pydantic_settings import BaseSettings

from app.config.setting import Settings, settings

from ..internal_auth import get_bot_settings

DEV_TOKEN_CONTEXT = b"yohaneowo-movies-site-v1"


class MovieSettings(BaseSettings):
    model_config = Settings.model_config
    WEBSITE_MOVIES_TOKEN: str = ""


@lru_cache
def site_token() -> str:
    configured = MovieSettings().WEBSITE_MOVIES_TOKEN
    if configured:
        return configured
    # Local development can derive a read-only key without exposing the bot's write key.
    if settings.ENVIRONMENT == "dev" and get_bot_settings().BOT_API_TOKEN:
        return hmac.new(get_bot_settings().BOT_API_TOKEN.encode(), DEV_TOKEN_CONTEXT, hashlib.sha256).hexdigest()
    return ""


async def verify_site_token(x_movies_token: Annotated[str | None, Header()] = None) -> None:
    expected = site_token()
    if not expected:
        raise HTTPException(503, "网站电影接口尚未配置")
    if not x_movies_token or not hmac.compare_digest(expected.encode(), x_movies_token.encode()):
        raise HTTPException(401, "无效的电影接口凭据")
