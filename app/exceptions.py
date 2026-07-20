class TicketMonitorError(Exception):
    """系统基础异常。"""


class ConfigurationError(TicketMonitorError):
    """配置错误。"""


class PlatformError(TicketMonitorError):
    """平台操作错误。"""


class AdapterNotImplementedError(PlatformError):
    """真实平台页面适配尚未完成。"""


class LoginRequiredError(PlatformError):
    """平台登录已失效。"""


class LoginTimeoutError(PlatformError):
    """等待用户登录超时。"""


class RetryablePlatformError(PlatformError):
    """可以稍后重试的平台异常。"""


class RateLimitError(PlatformError):
    """平台限流或风控。"""


class NotificationError(TicketMonitorError):
    """通知发送失败。"""
