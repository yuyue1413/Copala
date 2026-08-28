"""User-facing output sanitization for the Agent runtime."""

from __future__ import annotations


def looks_like_backend_error(content: str) -> bool:
    lowered = content.lower()
    sensitive_markers = (
        "error code:", "traceback", "stack trace", "api_key", "apikey", "authorization",
        "bearer ", "quota", "rate limit", "rate_limit", "litellm", "openai",
        "deepseek-ai/", "httpstatuserror", "client error", "server error",
    )
    if lowered.startswith(("error:", "exception:", "traceback")):
        return True
    return any(marker in lowered for marker in sensitive_markers)


def safe_user_error(raw: str | None = None) -> str:
    """Return a friendly error while raw provider details stay in logs."""
    text = raw or ""
    lowered = text.lower()
    if "429" in text or "quota" in lowered or "rate limit" in lowered or "rate_limit" in lowered:
        return "我这边的模型服务暂时额度不足或请求过于频繁，稍后再试一下就好。"
    if "timeout" in lowered or "timed out" in lowered:
        return "我这边响应超时了，稍后再试一下。"
    return "我这边处理时遇到了一点问题，已经记录到后台日志里了。"


def sanitize_user_facing_content(content: str) -> str:
    """Hide backend/provider details from normal chat responses."""
    text = content or ""
    return safe_user_error(text) if looks_like_backend_error(text) else text


def looks_like_unverified_calendar_write_claim(content: str, tools_used: list[str]) -> bool:
    if any(tool in {"calendar", "life_event"} for tool in tools_used):
        return False
    text = content or ""
    has_calendar = any(word in text for word in ("日程", "日历", "飞书日历", "飞书"))
    has_write_claim = any(
        word in text
        for word in (
            "已写入", "写入成功", "已创建", "创建成功", "已记录", "已经记录",
            "安排好了", "已安排", "添加成功",
        )
    )
    return has_calendar and has_write_claim
