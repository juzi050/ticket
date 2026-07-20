from __future__ import annotations

from urllib.parse import urlsplit

from app.config import MonitorTask, Settings
from app.database import Database
from app.logger import task_logger
from app.models import NotificationMessage, PreflightCheck, PreflightResult, TicketInfo
from app.platforms.base import TicketPlatform
from app.services.notification_service import NotificationService


class PreflightService:
    def __init__(
        self, settings: Settings, database: Database, notifications: NotificationService
    ) -> None:
        self.settings = settings
        self.database = database
        self.notifications = notifications

    async def run(self, task: MonitorTask, platform: TicketPlatform) -> PreflightResult:
        checks: list[PreflightCheck] = []
        logger = task_logger("app.preflight", task.task_id, task.platform)

        def add(name: str, passed: bool, message: str) -> None:
            checks.append(PreflightCheck(name, passed, message))

        logged_in = await platform.check_login_status()
        add("登录状态有效", logged_in, "已登录" if logged_in else "登录状态无效")

        parsed = urlsplit(task.event_url)
        valid_url = parsed.scheme in {"http", "https"} and bool(parsed.netloc)

        discovered_event_id = ""
        query_error = ""
        tickets: list[TicketInfo] = []
        try:
            event = await platform.search_event(task)
            discovered_event_id = str((event or {}).get("event_id", ""))
            tickets = list(await platform.preflight_tickets(task))
        except Exception as exc:
            query_error = str(exc)

        link_ok = valid_url and not query_error and bool(discovered_event_id)
        add(
            "演出链接有效",
            link_ok,
            task.event_url if link_ok else query_error or "页面未返回有效演出 ID",
        )

        event_names = {ticket.event_name for ticket in tickets if ticket.event_name}
        configured_event = "".join(task.event_name.split()).casefold()
        event_name_ok = any(
            configured_event in "".join(name.split()).casefold()
            or "".join(name.split()).casefold() in configured_event
            for name in event_names
        )
        add(
            "演出名称正确",
            event_name_ok,
            f"配置={task.event_name}，页面={', '.join(sorted(event_names)) or '未知'}",
        )

        event_id_ok = bool(task.event_id and discovered_event_id == task.event_id)
        add(
            "演出 ID 正确",
            event_id_ok,
            f"配置={task.event_id or '空'}，页面={discovered_event_id or '未知'}",
        )

        session_tickets = [
            ticket
            for ticket in tickets
            if (not task.target_sessions or ticket.session_name in task.target_sessions
                or any(value in ticket.session_name for value in task.target_sessions))
            and (not task.target_session_id or ticket.session_id == task.target_session_id)
        ]
        add(
            "目标场次存在",
            bool(session_tickets),
            "已定位" if session_tickets else query_error or "未找到目标场次",
        )
        session_ids = {ticket.session_id for ticket in session_tickets if ticket.session_id}
        configured_session_ok = bool(task.target_session_id) or not self.settings.strict_lock.strict_session_id
        add(
            "目标场次 ID 已确定",
            len(session_ids) == 1 and configured_session_ok,
            next(iter(session_ids)) if len(session_ids) == 1 and configured_session_ok else "场次 ID 缺失、未配置或不唯一",
        )

        level_tickets = [
            ticket
            for ticket in session_tickets
            if not task.target_ticket_levels
            or any(value in ticket.ticket_level for value in task.target_ticket_levels)
        ]
        add("目标票档存在", bool(level_tickets), "已定位" if level_tickets else "未找到目标票档")
        area_tickets = [
            ticket
            for ticket in level_tickets
            if not task.target_areas
            or any(
                area.replace(" ", "").casefold()
                in (ticket.area or "").replace(" ", "").casefold()
                for area in task.target_areas
            )
        ]
        add(
            "目标区域存在",
            bool(area_tickets),
            "已定位" if area_tickets else "未找到目标区域",
        )
        stable = [
            ticket for ticket in area_tickets
            if ticket.listing_id
            and (not task.target_listing_id or ticket.listing_id == task.target_listing_id)
            and (
                not task.target_ticket_group_id
                or ticket.ticket_group_id == task.target_ticket_group_id
            )
        ]
        configured_listing_ok = bool(task.target_listing_id) or not self.settings.strict_lock.strict_listing_id
        configured_group_ok = task.platform != "piaoniu" or bool(task.target_ticket_group_id)
        add(
            "目标票品能够稳定定位",
            bool(stable) and configured_listing_ok and configured_group_ok,
            (
                stable[0].listing_id
                if stable and configured_listing_ok and configured_group_ok
                else "listing_id 或票牛 ticket_group_id 缺失/未写入任务"
            ),
        )
        exact_quantity = [
            ticket
            for ticket in stable
            if int(ticket.raw.get("selected_quantity", ticket.raw.get("ticket_count", 0)))
            == task.quantity
        ]
        add(
            "目标数量能够精确选择",
            bool(exact_quantity),
            f"要求 {task.quantity} 张" if exact_quantity else f"没有精确的 {task.quantity} 张选项",
        )
        add("购买数量有效", task.quantity > 0, f"购买数量={task.quantity}")
        add(
            "价格上限有效",
            task.max_unit_price > 0 and task.max_total_price > 0,
            f"最高单价={task.max_unit_price}，最高总价={task.max_total_price}",
        )
        interval = task.interval_seconds or self.settings.monitor.default_interval_seconds
        add(
            "查询间隔合理",
            interval >= 1,
            f"查询间隔={interval}秒",
        )

        profile = self.settings.get_purchase_profile(task.purchase_profile_id)
        add("购票档案存在", profile is not None, task.purchase_profile_id or "任务未配置档案")
        audience_ok = bool(profile and len(profile.audiences) == task.quantity)
        add(
            "观演人数等于购票数量",
            audience_ok,
            f"观演人={len(profile.audiences) if profile else 0}，购票={task.quantity}",
        )
        contact_address_ok = bool(profile and profile.has_contact and profile.has_address)
        remote_profile_ok: bool | None = None
        remote_profile_message = "购票档案不存在"
        if profile:
            remote_profile_ok, remote_profile_message = await platform.validate_purchase_profile(
                profile, task.quantity
            )
        add(
            "联系人和地址已经存在",
            contact_address_ok and remote_profile_ok is True,
            (
                remote_profile_message
                if contact_address_ok
                else "联系人或地址引用缺失"
            ),
        )
        notice_ok = bool(profile and profile.accept_purchase_notice)
        add(
            "已接受购票须知",
            notice_ok,
            "已确认" if notice_ok else "购票档案未确认接受购票须知",
        )

        notification_configured = self.settings.notification.enabled and (
            self.settings.notification.provider != "console"
            or task.platform == "mock"
            or self.settings.application.mock_mode
        )
        notification_ok = False
        if notification_configured:
            notification_ok = await self.notifications.send(
                NotificationMessage(
                    "preflight",
                    "购票任务预检通知",
                    f"任务 {task.task_id} 正在校验通知渠道。",
                ),
                force=True,
            )
        add(
            "微信通知可用",
            notification_ok,
            "发送成功" if notification_ok else "通知未启用、仍为 console 或发送失败",
        )

        candidate = exact_quantity[0] if exact_quantity else None
        local_pending = False
        platform_pending: bool | None = None
        if profile and candidate:
            local_pending = await self.database.has_pending_order(
                account_alias=profile.account_alias,
                platform=candidate.platform,
                event_id=candidate.event_id,
                session_id=candidate.session_id,
                listing_id=candidate.listing_id,
                quantity=task.quantity,
            )
            platform_pending = await platform.has_pending_order(
                task, candidate, profile.account_alias
            )
        no_pending = not local_pending and platform_pending is False
        pending_message = (
            "已存在待支付订单"
            if local_pending or platform_pending is True
            else "平台订单列表选择器尚未验证"
            if platform_pending is None
            else "本地与平台均无重复订单"
        )
        add("没有相同待支付订单", no_pending, pending_message)

        try:
            await self.database.list_task_states()
            database_ok = True
        except Exception:
            database_ok = False
        add("浏览器和数据库状态正常", logged_in and database_ok, "正常" if logged_in and database_ok else "异常")
        for check in checks:
            logger.info(
                "预检 [%s] %s：%s",
                "通过" if check.passed else "失败",
                check.name,
                check.message,
            )
        return PreflightResult(task.task_id, checks, candidate)
