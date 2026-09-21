"""Unit tests for FlowKit Client API v1 error handling and translation."""
import pytest
from fastapi import HTTPException

from agent.api.v1.errors import (
    V1ErrorCode,
    parse_v1_error,
    raise_v1_http_error,
    build_v1_job_error,
)
from agent.api.v1.schemas import JobError


def test_parse_v1_error_safety_violation():
    raw = "RpcError: ('wbgUpc', [5])"
    parsed = parse_v1_error(raw)
    assert parsed["code"] == V1ErrorCode.SAFETY_OR_POLICY_VIOLATION
    assert "kiểm duyệt an toàn" in parsed["message"]
    assert "điều chỉnh lại prompt" in parsed["action"]
    assert parsed["http_status"] == 422
    assert parsed["retryable"] is False
    assert parsed["upstream_code"] == "5"


def test_parse_v1_error_quota_exceeded():
    raw = "FlowBatchError: wbgUpc: public_error_per_model_daily_quota_reached"
    parsed = parse_v1_error(raw)
    assert parsed["code"] == V1ErrorCode.QUOTA_EXCEEDED
    assert "quota" in parsed["message"].lower()
    assert parsed["http_status"] == 429
    assert parsed["retryable"] is False


def test_parse_v1_error_no_flow_tab():
    raw = "FlowBatchError: wbgUpc: NO_FLOW_TAB"
    parsed = parse_v1_error(raw)
    assert parsed["code"] == V1ErrorCode.NO_FLOW_TAB
    assert "Không tìm thấy tab Google Flow" in parsed["message"]
    assert parsed["http_status"] == 503
    assert parsed["retryable"] is True


def test_parse_v1_error_extension_not_connected():
    raw = "Flow extension is not connected"
    parsed = parse_v1_error(raw)
    assert parsed["code"] == V1ErrorCode.EXTENSION_NOT_CONNECTED
    assert "chưa được kết nối" in parsed["message"]
    assert parsed["http_status"] == 503
    assert parsed["retryable"] is True


def test_parse_v1_error_session_expired():
    raw = "HTTP 401 Unauthorized from Google Flow: Session expired on profile Flow tab."
    parsed = parse_v1_error(raw)
    assert parsed["code"] == V1ErrorCode.SESSION_EXPIRED
    assert "hết hạn" in parsed["message"]
    assert parsed["http_status"] == 401
    assert parsed["retryable"] is False


def test_parse_v1_error_captcha_failed():
    raw = "FlowBatchError: wbgUpc: CAPTCHA_FAILED: grecaptcha not available"
    parsed = parse_v1_error(raw)
    assert parsed["code"] == V1ErrorCode.CAPTCHA_FAILED
    assert "reCAPTCHA" in parsed["message"]
    assert parsed["http_status"] == 502
    assert parsed["retryable"] is True


def test_parse_v1_error_timeout():
    raw = "Omni Flash video generation timed out after 180s"
    parsed = parse_v1_error(raw)
    assert parsed["code"] == V1ErrorCode.TIMEOUT
    assert "thời gian chờ" in parsed["message"]
    assert parsed["http_status"] == 504
    assert parsed["retryable"] is True


def test_raise_v1_http_error():
    with pytest.raises(HTTPException) as exc_info:
        raise_v1_http_error("RpcError: ('wbgUpc', [5])")
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == V1ErrorCode.SAFETY_OR_POLICY_VIOLATION
    assert "details" in detail
    assert "action" in detail


def test_build_v1_job_error():
    job_err = build_v1_job_error("RpcError: ('s0x08', [5])", op_id="operations/video-123")
    assert isinstance(job_err, JobError)
    assert job_err.code == V1ErrorCode.SAFETY_OR_POLICY_VIOLATION
    assert "operations/video-123" in job_err.message
    assert "RpcError" in job_err.details
    assert job_err.upstream_code == "5"
    assert job_err.retryable is False
