from __future__ import annotations

import os
from abc import ABC, abstractmethod

import httpx

from app.config import NotificationSettings
from app.exceptions import ConfigurationError, NotificationError
from app.models import NotificationMessage


class Notifier(ABC):
    provider: str

    @abstractmethod
    async def send(self, message: NotificationMessage) -> bool:
        """发送一次通知，失败时抛出异常。"""

    async def close(self) -> None:
        return None


class ConsoleNotifier(Notifier):
    provider = "console"

    async def send(self, message: NotificationMessage) -> bool:
        print(f"\n【{message.title}】\n{message.content}\n")
        return True


class HttpNotifier(Notifier):
    def __init__(self, provider: str, secret: str) -> None:
        self.provider = provider
        self.secret = secret
        self.client = httpx.AsyncClient(timeout=15)

    async def send(self, message: NotificationMessage) -> bool:
        try:
            if self.provider == "wechat_work":
                response = await self.client.post(
                    self.secret,
                    json={"msgtype": "text", "text": {"content": f"【{message.title}】\n{message.content}"}},
                )
            elif self.provider == "serverchan":
                response = await self.client.post(
                    f"https://sctapi.ftqq.com/{self.secret}.send",
                    data={"title": message.title, "desp": message.content},
                )
            elif self.provider == "pushplus":
                response = await self.client.post(
                    "https://www.pushplus.plus/send",
                    json={"token": self.secret, "title": message.title, "content": message.content},
                )
            else:
                raise NotificationError(f"不支持的通知渠道：{self.provider}")
            response.raise_for_status()
            payload = response.json()
            code = payload.get("errcode", payload.get("code", 0))
            if str(code) not in {"0", "200"}:
                raise NotificationError(f"通知服务返回失败状态：{code}")
            return True
        except (httpx.HTTPError, ValueError) as exc:
            raise NotificationError(f"通知请求失败：{exc}") from exc

    async def close(self) -> None:
        await self.client.aclose()


def build_notifier(settings: NotificationSettings, *, mock_mode: bool = False) -> Notifier:
    if mock_mode or settings.provider == "console" or not settings.enabled:
        return ConsoleNotifier()
    env_names = {
        "wechat_work": "WECHAT_WORK_WEBHOOK",
        "serverchan": "SERVERCHAN_SENDKEY",
        "pushplus": "PUSHPLUS_TOKEN",
    }
    env_name = env_names[settings.provider]
    secret = os.getenv(env_name, "").strip()
    if not secret:
        raise ConfigurationError(f"通知渠道 {settings.provider} 缺少环境变量 {env_name}")
    return HttpNotifier(settings.provider, secret)
