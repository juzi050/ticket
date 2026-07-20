from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.domain import (
    BuyerProfile,
    EventInfo,
    MonitorTask,
    OrderPreview,
    OrderResult,
    PlatformName,
    SessionInfo,
    TicketOption,
)
from app.storage.audit_repository import AuditEntry, AuditRepository
from app.storage.buyer_repository import BuyerBindingRepository
from app.storage.session_repository import PlatformSessionRepository


class PlatformApiError(RuntimeError):
    pass


class PlatformAuthExpiredError(PlatformApiError):
    pass


class PlatformCapabilityUnavailable(PlatformApiError):
    pass


class TicketPlatformApi(ABC):
    platform: PlatformName

    def __init__(
        self,
        client: httpx.AsyncClient,
        audit_repository: AuditRepository,
        session_repository: PlatformSessionRepository | None = None,
        buyer_binding_repository: BuyerBindingRepository | None = None,
    ) -> None:
        self.client = client
        self.audit = audit_repository
        self.sessions = session_repository
        self.buyer_bindings = buyer_binding_repository

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        action: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        requires_auth: bool = False,
    ) -> Any:
        try:
            response = await self.client.request(
                method, url, params=params, json=json_body
            )
            is_json = "json" in response.headers.get("content-type", "").lower()
            if is_json:
                try:
                    body = response.json()
                except ValueError:
                    body = response.text
                    is_json = False
            else:
                body = response.text
            await self.audit.append(
                AuditEntry(
                    level="INFO" if response.is_success else "ERROR",
                    category="http",
                    action=action,
                    platform=self.platform,
                    message=f"{method.upper()} {action} -> HTTP {response.status_code}",
                    request_url=str(response.request.url),
                    request_method=method.upper(),
                    request_headers=dict(response.request.headers),
                    request_body=json_body,
                    response_status=response.status_code,
                    response_headers=dict(response.headers),
                    response_body=body,
                )
            )
            if response.status_code in {401, 403} and requires_auth:
                if self.sessions:
                    await self.sessions.mark_expired(self.platform)
                raise PlatformAuthExpiredError("登录状态已失效，请重新登录")
            response.raise_for_status()
            if not is_json:
                raise PlatformApiError("平台返回了非 JSON 响应")
            return body
        except PlatformApiError:
            raise
        except Exception as exc:
            await self.audit.append(
                AuditEntry(
                    level="ERROR",
                    category="http",
                    action=action,
                    platform=self.platform,
                    message="平台 HTTP 请求失败",
                    request_url=url,
                    request_method=method.upper(),
                    request_body=json_body,
                    exception_type=type(exc).__name__,
                    exception_message=str(exc),
                )
            )
            raise PlatformApiError(f"{self.platform} {action} 请求失败：{exc}") from exc

    @abstractmethod
    async def check_auth(self) -> bool: ...

    @abstractmethod
    async def get_event(self, event_url: str) -> EventInfo: ...

    @abstractmethod
    async def list_sessions(self, event_id: str) -> list[SessionInfo]: ...

    @abstractmethod
    async def list_tickets(
        self, event_id: str, session_id: str, quantity: int
    ) -> list[TicketOption]: ...

    @abstractmethod
    async def get_exact_ticket(
        self, ticket: TicketOption, quantity: int
    ) -> TicketOption | None: ...

    @abstractmethod
    async def ensure_remote_buyers(self, buyers: list[BuyerProfile]) -> list[str]: ...

    @abstractmethod
    async def preview_order(
        self, ticket: TicketOption, quantity: int, buyers: list[BuyerProfile]
    ) -> OrderPreview: ...

    @abstractmethod
    async def create_order(self, preview: OrderPreview) -> OrderResult: ...

    @abstractmethod
    async def get_order_detail(self, order_id: str) -> OrderResult: ...

    @abstractmethod
    async def find_recent_order(self, task: MonitorTask) -> OrderResult | None: ...

    async def close(self) -> None:
        await self.client.aclose()
