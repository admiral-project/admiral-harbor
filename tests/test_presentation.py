from datetime import date, datetime, timezone

from app.presentation import format_billing_date


def test_format_billing_date_handles_current_and_legacy_values():
    assert format_billing_date("2026-12-31") == "2026-12-31"
    assert format_billing_date(datetime(2026, 12, 31, 15, 30)) == "2026-12-31"
    assert format_billing_date(datetime(2026, 12, 31, 15, 30, tzinfo=timezone.utc)) == "2026-12-31"
    assert format_billing_date(date(2026, 12, 31)) == "2026-12-31"
    assert format_billing_date("not a date") == "not a date"
    assert format_billing_date(None, "-") == "-"
