from __future__ import annotations

from decimal import Decimal
import os
from pathlib import Path
import re
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.exceptions import ConfigurationError


class ApplicationSettings(BaseModel):
    log_level: str = "INFO"
    database_path: Path = Path("data/ticket_monitor.db")
    timezone: str = "Asia/Shanghai"
    mock_mode: bool = False


class BrowserSettings(BaseModel):
    headless: bool = False
    channel: str | None = "msedge"
    executable_path: str | None = None
    page_timeout_seconds: int = Field(default=30, ge=5)
    close_after_login: bool = True


class LoginSettings(BaseModel):
    timeout_seconds: int = Field(default=600, ge=1)
    retry_interval_seconds: int = Field(default=300, ge=1)
    check_interval_seconds: float = Field(default=2, ge=0.1)
    auto_open_login_page: bool = True


class NotificationSettings(BaseModel):
    enabled: bool = True
    provider: Literal["wechat_work", "serverchan", "pushplus", "console"] = "console"
    max_retries: int = Field(default=3, ge=1, le=10)
    retry_interval_seconds: float = Field(default=5, ge=0)


class MonitorSettings(BaseModel):
    default_interval_seconds: float = Field(default=10, ge=1)
    random_delay_min_seconds: float = Field(default=1, ge=0)
    random_delay_max_seconds: float = Field(default=3, ge=0)
    max_consecutive_errors: int = Field(default=5, ge=1)
    lock_cooldown_seconds: int = Field(default=60, ge=0)

    @model_validator(mode="after")
    def validate_delay(self) -> "MonitorSettings":
        if self.random_delay_max_seconds < self.random_delay_min_seconds:
            raise ValueError("random_delay_max_seconds 不能小于 random_delay_min_seconds")
        return self


class StrictLockSettings(BaseModel):
    strict_quantity: bool = True
    strict_session_id: bool = True
    strict_listing_id: bool = True
    strict_audience_count: bool = True
    reject_unknown_final_price: bool = True
    reject_listing_replacement: bool = True
    max_price_slippage: Decimal = Field(default=Decimal("0"), ge=0)
    stop_before_payment: bool = True
    stage_timeout_seconds: int = Field(default=30, ge=5)


class PurchaseAudience(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = ""
    platform_option_id: str = ""
    phone_last4: str = ""

    @model_validator(mode="after")
    def require_saved_option(self) -> "PurchaseAudience":
        if not self.name and not self.platform_option_id:
            raise ValueError("观演人必须填写 name 或 platform_option_id")
        if self.phone_last4 and not re.fullmatch(r"\d{4}", self.phone_last4):
            raise ValueError("phone_last4 必须是 4 位数字")
        return self


class PurchaseProfile(BaseModel):
    """仅保存平台已有选项的引用，不保存身份证、密码或支付信息。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    profile_id: str = Field(min_length=1)
    account_alias: str = Field(min_length=1)
    audiences: list[PurchaseAudience] = Field(default_factory=list)
    contact_id: str = ""
    contact_name: str = ""
    contact_phone_last4: str = ""
    address_id: str = ""
    address_label: str = ""
    address_phone_last4: str = ""
    delivery_method: str = ""
    accept_purchase_notice: bool = False

    @field_validator("contact_phone_last4", "address_phone_last4")
    @classmethod
    def validate_phone_last4(cls, value: str) -> str:
        if value and not re.fullmatch(r"\d{4}", value):
            raise ValueError("手机号后四位必须是 4 位数字")
        return value

    @property
    def has_contact(self) -> bool:
        return bool(self.contact_id or (self.contact_name and self.contact_phone_last4))

    @property
    def has_address(self) -> bool:
        return bool(self.address_id or (self.address_label and self.address_phone_last4))


class PlatformAutomationSettings(BaseModel):
    """真实站点可验证的页面规则；为空时适配器会保守地判定为未登录。"""

    home_url: str
    login_url: str | None = None
    auth_check_url: str | None = None
    login_trigger_text: str | None = None
    authenticated_selectors: list[str] = Field(default_factory=list)
    unauthenticated_selectors: list[str] = Field(default_factory=list)


class MonitorTask(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    task_id: str = Field(min_length=1)
    enabled: bool = True
    platform: Literal["piaoniu", "motianlun", "mock"]
    event_name: str = Field(min_length=1)
    event_url: str = Field(min_length=1)
    event_id: str = ""
    target_session_id: str = ""
    target_listing_id: str = ""
    target_ticket_group_id: str = ""
    target_sessions: list[str] = Field(default_factory=list)
    event_date: str | None = None
    event_time: str | None = None
    target_ticket_levels: list[str] = Field(default_factory=list)
    target_areas: list[str] = Field(default_factory=list)
    target_stands: list[str] = Field(default_factory=list)
    target_seat_positions: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    area_regexes: list[str] = Field(default_factory=list)
    match_mode: Literal["exact", "contains"] = "contains"
    area_priorities: dict[str, int] = Field(default_factory=dict)
    row_min: int | None = Field(default=None, ge=0)
    row_max: int | None = Field(default=None, ge=0)
    seat_min: int | None = Field(default=None, ge=0)
    seat_max: int | None = Field(default=None, ge=0)
    quantity: int = Field(default=1, ge=1)
    adjacent_seats_required: bool = False
    max_unit_price: Decimal = Field(gt=0)
    max_total_price: Decimal = Field(gt=0)
    interval_seconds: float | None = Field(default=None, ge=1)
    random_delay_min_seconds: float | None = Field(default=None, ge=0)
    random_delay_max_seconds: float | None = Field(default=None, ge=0)
    auto_lock: bool = False
    notify: bool = True
    stop_after_lock_success: bool = True
    max_lock_attempts: int = Field(default=1, ge=1)
    max_consecutive_errors: int | None = Field(default=None, ge=1)
    purchase_profile_id: str = ""

    @field_validator("target_sessions", "target_ticket_levels", "target_areas", "target_stands", "target_seat_positions", "excluded_keywords", "area_regexes")
    @classmethod
    def remove_empty_candidates(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]

    @field_validator("area_regexes")
    @classmethod
    def validate_regexes(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"无效区域正则 {pattern!r}：{exc}") from exc
        return value

    @model_validator(mode="after")
    def validate_ranges_and_prices(self) -> "MonitorTask":
        if self.row_min is not None and self.row_max is not None and self.row_max < self.row_min:
            raise ValueError("row_max 不能小于 row_min")
        if self.seat_min is not None and self.seat_max is not None and self.seat_max < self.seat_min:
            raise ValueError("seat_max 不能小于 seat_min")
        if (
            self.random_delay_min_seconds is not None
            and self.random_delay_max_seconds is not None
            and self.random_delay_max_seconds < self.random_delay_min_seconds
        ):
            raise ValueError("任务随机延迟上限不能小于下限")
        return self


class Settings(BaseModel):
    application: ApplicationSettings = Field(default_factory=ApplicationSettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    login: LoginSettings = Field(default_factory=LoginSettings)
    notification: NotificationSettings = Field(default_factory=NotificationSettings)
    monitor: MonitorSettings = Field(default_factory=MonitorSettings)
    strict_lock: StrictLockSettings = Field(default_factory=StrictLockSettings)
    purchase_profiles_file: Path = Path("purchase_profiles.yaml")
    purchase_profiles: list[PurchaseProfile] = Field(default_factory=list)
    platforms: dict[str, PlatformAutomationSettings] = Field(default_factory=dict)
    tasks: list[MonitorTask]

    @model_validator(mode="after")
    def unique_task_ids(self) -> "Settings":
        ids = [task.task_id for task in self.tasks]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"task_id 重复：{', '.join(duplicates)}")
        profile_ids = [profile.profile_id for profile in self.purchase_profiles]
        duplicate_profiles = sorted({item for item in profile_ids if profile_ids.count(item) > 1})
        if duplicate_profiles:
            raise ValueError(f"profile_id 重复：{', '.join(duplicate_profiles)}")
        return self

    def get_purchase_profile(self, profile_id: str) -> PurchaseProfile | None:
        return next((item for item in self.purchase_profiles if item.profile_id == profile_id), None)


def load_settings(path: str | Path = "config.yaml", *, allow_example: bool = False) -> Settings:
    load_dotenv(override=False)
    config_path = Path(path)
    if not config_path.exists() and allow_example:
        config_path = Path("config.example.yaml")
    if not config_path.exists():
        raise ConfigurationError(f"配置文件不存在：{config_path}。请先复制 config.example.yaml 为 config.yaml")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        profiles_file = Path(raw.get("purchase_profiles_file", "purchase_profiles.yaml"))
        if not profiles_file.is_absolute():
            profiles_file = config_path.parent / profiles_file
        if profiles_file.exists():
            profile_raw = yaml.safe_load(profiles_file.read_text(encoding="utf-8")) or {}
            raw["purchase_profiles"] = profile_raw.get("purchase_profiles", profile_raw.get("profiles", []))
        settings = Settings.model_validate(raw)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"YAML 格式错误：{exc}") from exc
    except ValidationError as exc:
        lines = [f"- {'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()]
        raise ConfigurationError("配置校验失败：\n" + "\n".join(lines)) from exc
    browser_channel = os.getenv("BROWSER_CHANNEL", "").strip()
    browser_path = os.getenv("BROWSER_EXECUTABLE_PATH", "").strip()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if browser_channel:
        settings.browser.channel = browser_channel
    if browser_path:
        settings.browser.executable_path = browser_path
    if database_url.startswith("sqlite:///"):
        settings.application.database_path = Path(database_url.removeprefix("sqlite:///"))
    return settings
