"""Centralized error handling and translation for FlowKit Client API v1 (/v1/...).

Translates raw upstream errors from Google Flow, the Chrome Extension, and background workers
into clear, actionable Vietnamese error messages with standardized error codes.
"""
from __future__ import annotations

import re
from typing import Any
from fastapi import HTTPException

from agent.api.v1.schemas import JobError


class V1ErrorCode:
    SAFETY_OR_POLICY_VIOLATION = "SAFETY_OR_POLICY_VIOLATION"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NO_FLOW_TAB = "NO_FLOW_TAB"
    EXTENSION_NOT_CONNECTED = "EXTENSION_NOT_CONNECTED"
    CAPTCHA_FAILED = "CAPTCHA_FAILED"
    UPSTREAM_NO_RESPONSE = "UPSTREAM_NO_RESPONSE"
    MEDIA_NOT_GENERATED = "MEDIA_NOT_GENERATED"
    TIMEOUT = "TIMEOUT"
    IMAGE_DOWNLOAD_FAILED = "IMAGE_DOWNLOAD_FAILED"
    MEDIA_UPLOAD_FAILED = "MEDIA_UPLOAD_FAILED"
    GENERATION_FAILED = "GENERATION_FAILED"


def parse_v1_error(
    raw_error: Any,
    default_message: str = "Tác vụ thất bại",
    default_action: str | None = None,
) -> dict[str, Any]:
    """Parse any error (string, dict, Exception, HTTPException) into a structured dictionary."""
    if isinstance(raw_error, HTTPException):
        if isinstance(raw_error.detail, dict) and "code" in raw_error.detail:
            code = raw_error.detail.get("code", V1ErrorCode.GENERATION_FAILED)
            msg = raw_error.detail.get("message", str(raw_error.detail))
            details = raw_error.detail.get("details", str(raw_error.detail))
            action = raw_error.detail.get("action", default_action or "Vui lòng kiểm tra lại yêu cầu.")
            return {
                "code": code,
                "message": msg,
                "details": details,
                "action": action,
                "http_status": raw_error.status_code,
                "retryable": code in (V1ErrorCode.TIMEOUT, V1ErrorCode.CAPTCHA_FAILED, V1ErrorCode.UPSTREAM_NO_RESPONSE),
                "upstream_code": None,
                "detail": {"code": code, "message": msg, "details": details, "action": action},
            }
        raw_str = str(raw_error.detail)
        http_code_hint = raw_error.status_code
    elif isinstance(raw_error, dict):
        raw_str = str(raw_error.get("error") or raw_error.get("detail") or raw_error.get("message") or raw_error)
        http_code_hint = raw_error.get("status") if isinstance(raw_error.get("status"), int) else None
    elif isinstance(raw_error, Exception):
        raw_str = f"{type(raw_error).__name__}: {raw_error}"
        http_code_hint = None
    else:
        raw_str = str(raw_error or "")
        http_code_hint = None

    lower = raw_str.lower()
    details = raw_str

    # 1. Google Flow Safety / Policy Violation: RPC error code [5]
    # In Flow's batchexecute, code 5 represents NOT_FOUND/PERMISSION_DENIED/Policy Violation on generation
    if (
        re.search(r"\[\s*5\s*\]", raw_str)
        or "kiểm duyệt" in lower
        or "vi phạm" in lower
        or "policy" in lower
        or "safety" in lower
        or "từ chối tạo video" in lower
    ):
        code = V1ErrorCode.SAFETY_OR_POLICY_VIOLATION
        message = (
            "Google Flow từ chối yêu cầu (mã lỗi [5] - Thường do nội dung prompt hoặc ảnh tham chiếu vi phạm "
            "chính sách kiểm duyệt an toàn của Google, hoặc tài khoản bị giới hạn)."
        )
        action = "Vui lòng điều chỉnh lại prompt (dùng từ ngữ trung tính, tránh nhạy cảm/bạo lực) hoặc đổi ảnh tham chiếu và thử lại."
        http_status = 422
        retryable = False
        upstream_code = "5"

    # 2. Quota Exceeded: RPC error code [8] or explicit quota errors
    elif (
        re.search(r"\[\s*8\s*\]", raw_str)
        or "quota" in lower
        or "hết lượt" in lower
        or "user_quota_reached" in lower
        or "per_model_daily_quota_reached" in lower
    ):
        code = V1ErrorCode.QUOTA_EXCEEDED
        message = "Tài khoản Google Flow đã đạt giới hạn quota tạo (hết hạn mức trong ngày hoặc giới hạn của model)."
        action = "Vui lòng chờ sang ngày hôm sau để hệ thống reset quota hoặc đổi sang tài khoản Google khác."
        http_status = 429
        retryable = False
        upstream_code = "8"

    # 3. Invalid Argument: RPC error code [3]
    elif re.search(r"\[\s*3\s*\]", raw_str) or "invalid argument" in lower or "invalid_argument" in lower:
        code = V1ErrorCode.INVALID_ARGUMENT
        message = "Tham số gửi tới Google Flow không hợp lệ (mã lỗi [3] - Invalid Argument)."
        action = "Vui lòng kiểm tra lại tỷ lệ khung hình, model hoặc cấu trúc prompt."
        http_status = 400
        retryable = False
        upstream_code = "3"

    # 4. Session Expired / Unauthenticated: 401, NO_AT_TOKEN, RPC code [16]
    elif (
        re.search(r"\[\s*16\s*\]", raw_str)
        or "401" in lower
        or "unauthorized" in lower
        or "session expired" in lower
        or "no_at_token" in lower
        or "phiên đăng nhập" in lower
    ):
        code = V1ErrorCode.SESSION_EXPIRED
        message = "Phiên đăng nhập trên Google Flow đã hết hạn hoặc chưa được xác thực (token AT/WIZ không khả dụng)."
        action = "Vui lòng mở lại tab Google Flow (flow.google.com), tải lại trang (F5) và đăng nhập lại tài khoản Google."
        http_status = 401
        retryable = False
        upstream_code = "401"

    # 5. Permission Denied / Forbidden: 403
    elif "403" in lower or "forbidden" in lower or "permission denied" in lower:
        code = V1ErrorCode.PERMISSION_DENIED
        message = "Tài khoản Google Flow hiện tại không có quyền truy cập vào project hoặc tài nguyên này."
        action = "Kiểm tra tài khoản Google trên tab Flow xem có quyền truy cập project hay không."
        http_status = 403
        retryable = False
        upstream_code = "403"

    # 6. No Flow Tab open in Chrome
    elif "no_flow_tab" in lower or "no flow tab" in lower or "flow_tab_discarded" in lower:
        code = V1ErrorCode.NO_FLOW_TAB
        message = "Không tìm thấy tab Google Flow (flow.google.com) nào đang mở trong trình duyệt Chrome."
        action = "Vui lòng mở tab https://flow.google.com trên Chrome, đăng nhập tài khoản Google và giữ tab mở khi sử dụng."
        http_status = 503
        retryable = True
        upstream_code = None

    # 7. Extension not connected
    elif (
        "extension is not connected" in lower
        or "no_extension" in lower
        or "extension not connected" in lower
        or "extension disconnected" in lower
    ):
        code = V1ErrorCode.EXTENSION_NOT_CONNECTED
        message = "Chrome Extension FlowKit chưa được kết nối với backend server."
        action = "Vui lòng mở trình duyệt Chrome có cài extension FlowKit và kiểm tra trạng thái kết nối của extension."
        http_status = 503
        retryable = True
        upstream_code = None

    # 8. Captcha solving failure
    elif (
        "captcha" in lower
        or "no_token" in lower
        or "grecaptcha not available" in lower
        or "content_timeout" in lower
    ):
        code = V1ErrorCode.CAPTCHA_FAILED
        message = "Không thể giải mã reCAPTCHA trên tab Google Flow (thời gian chờ quá lâu hoặc reCAPTCHA chưa sẵn sàng)."
        action = "Đảm bảo tab flow.google.com không bị treo hoặc reload lại tab flow.google.com."
        http_status = 502
        retryable = True
        upstream_code = None

    # 9. Upstream RPC envelope missing / empty response
    elif (
        "no wbgupc envelope" in lower
        or "no maseq envelope" in lower
        or "no ogiz0b envelope" in lower
        or "no sprrcad envelope" in lower
        or "envelope in response" in lower
    ):
        code = V1ErrorCode.UPSTREAM_NO_RESPONSE
        message = "Google Flow không phản hồi dữ liệu kết quả RPC hợp lệ (có thể do lỗi mạng hoặc tab Flow đang reload)."
        action = "Kiểm tra lại kết nối mạng và tab Google Flow rồi thử lại."
        http_status = 502
        retryable = True
        upstream_code = None

    # 10. Completed but no media URL (post-generation moderation or dropped)
    elif (
        "no media url" in lower
        or "media not found" in lower
        or "returned no url" in lower
        or "returned no media items" in lower
    ):
        code = V1ErrorCode.MEDIA_NOT_GENERATED
        message = "Google Flow đã hoàn tất xử lý nhưng không trả về URL media (nội dung có thể bị hệ thống an toàn hậu kiểm huỷ bỏ)."
        action = "Thử lại với prompt khác hoặc giảm bớt chi tiết nhạy cảm."
        http_status = 502
        retryable = False
        upstream_code = None

    # 11. Timeout
    elif "timed out" in lower or "timeout" in lower:
        code = V1ErrorCode.TIMEOUT
        message = "Tác vụ tạo ảnh/video vượt quá thời gian chờ tối đa từ Google Flow."
        action = "Hệ thống Google Flow có thể đang quá tải hoặc hàng đợi render quá dài. Vui lòng thử lại sau vài phút."
        http_status = 504
        retryable = True
        upstream_code = None

    # 12. Image Download / URL Fetch failure
    elif "failed to fetch image" in lower or "error downloading image" in lower or "file source not reachable" in lower:
        code = V1ErrorCode.IMAGE_DOWNLOAD_FAILED
        message = "Không thể tải ảnh đầu vào từ URL đã cung cấp."
        action = "Vui lòng kiểm tra lại đường dẫn ảnh (image_url) xem có truy cập công khai được hay không."
        http_status = 400
        retryable = False
        upstream_code = None

    # 13. Default fallback
    else:
        code = V1ErrorCode.GENERATION_FAILED
        message = default_message if default_message != "Tác vụ thất bại" else (f"Xảy ra lỗi từ Google Flow: {raw_str}" if raw_str else default_message)
        action = default_action or "Vui lòng kiểm tra tab Google Flow hoặc thử lại sau."
        http_status = http_code_hint or 502
        retryable = False
        upstream_code = None

    return {
        "code": code,
        "message": message,
        "details": details,
        "action": action,
        "http_status": http_status,
        "retryable": retryable,
        "upstream_code": upstream_code,
        "detail": {
            "code": code,
            "message": message,
            "details": details,
            "action": action,
        },
    }


def raise_v1_http_error(
    raw_error: Any,
    status_code: int | None = None,
    default_message: str = "Tác vụ thất bại",
    default_action: str | None = None,
) -> None:
    """Parse error and raise a structured HTTPException for FastAPI endpoints."""
    parsed = parse_v1_error(raw_error, default_message=default_message, default_action=default_action)
    code = status_code or parsed["http_status"]
    raise HTTPException(status_code=code, detail=parsed["detail"])


def build_v1_job_error(err_msg: Any, op_id: str | None = None) -> JobError:
    """Construct a JobError schema instance from an error message or exception."""
    parsed = parse_v1_error(err_msg)
    user_msg = parsed["message"]
    if op_id and "operation" not in user_msg.lower():
        user_msg = f"{user_msg} (Google Operation: {op_id})"

    return JobError(
        code=parsed["code"],
        message=user_msg,
        details=parsed["details"],
        retryable=parsed["retryable"],
        upstream_code=parsed.get("upstream_code"),
    )
