"""bot 内部 API 的鉴权：bot 以 X-Bot-Token 请求头携带共享密钥，不走后台登录。"""

import hmac
from functools import lru_cache
from typing import Annotated

from fastapi import Header, HTTPException, status
from pydantic_settings import BaseSettings

from app.config.setting import Settings


class BotSettings(BaseSettings):
    """插件自己的配置，与框架共用同一份 env 文件，避免改动框架的 setting.py。"""

    model_config = Settings.model_config

    # 生成：openssl rand -hex 32；bot 端 ADMIN_API_TOKEN 填同一个值。留空 = 关闭内部 API
    BOT_API_TOKEN: str = ""


@lru_cache
def get_bot_settings() -> BotSettings:
    return BotSettings()


async def verify_bot_token(x_bot_token: Annotated[str | None, Header()] = None) -> None:
    expected = get_bot_settings().BOT_API_TOKEN
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="未配置 BOT_API_TOKEN，bot 内部 API 已关闭")
    if not x_bot_token or not hmac.compare_digest(x_bot_token.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-Bot-Token 无效")
