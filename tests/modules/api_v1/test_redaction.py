from lightmes.modules.api_v1.middleware import sanitize_error_detail


def test_sanitize_redacts_api_key():
    detail = "Auth failed for lmk_live_abc123XYZ"
    sanitized = sanitize_error_detail(detail)
    assert "lmk_live_abc123XYZ" not in sanitized
    assert "REDACTED" in sanitized


def test_sanitize_redacts_password():
    detail = "Login failed: password=hunter2"
    sanitized = sanitize_error_detail(detail)
    assert "hunter2" not in sanitized


def test_sanitize_truncates_long_detail():
    detail = "x" * 500
    sanitized = sanitize_error_detail(detail)
    assert len(sanitized) == 200


def test_sanitize_handles_none():
    assert sanitize_error_detail(None) is None
