from datetime import date

from newscollector.ingestion import context
from newscollector.ingestion.context import HolidayEvent


def test_expand_manual_holiday_ranges_creates_daily_events():
    rows = [
        {
            "start": "2026-02-15",
            "end": "2026-02-17",
            "name": "Chinese New Year (Spring Festival)",
            "local_name": "春节",
        }
    ]
    events = context._expand_manual_holiday_ranges("CN", rows)

    assert [event.date.isoformat() for event in events] == [
        "2026-02-15",
        "2026-02-16",
        "2026-02-17",
    ]
    assert all(event.country_code == "CN" for event in events)
    assert all(event.name == "Chinese New Year (Spring Festival)" for event in events)


def test_fetch_upcoming_holidays_merges_manual_ranges_and_dedupes(monkeypatch):
    def _fake_fetch(_client, country_code: str, _year: int):
        if country_code == "CN":
            return [
                HolidayEvent(
                    country_code="CN",
                    date=date(2026, 2, 17),
                    local_name="春节",
                    name="Chinese New Year (Spring Festival)",
                    types=["Public"],
                )
            ], None
        return [], None

    monkeypatch.setattr(context, "_fetch_holidays_for_year", _fake_fetch)

    window, error = context.fetch_upcoming_holidays(
        country_codes=["CN"],
        window_days=2,
        reference_date=date(2026, 2, 16),
        manual_ranges={
            "CN": [
                {
                    "start": "2026-02-16",
                    "end": "2026-02-17",
                    "name": "Chinese New Year (Spring Festival)",
                    "local_name": "春节",
                }
            ]
        },
    )

    assert error is None
    events = window.by_country["CN"]
    assert [event.date.isoformat() for event in events] == ["2026-02-16", "2026-02-17"]
