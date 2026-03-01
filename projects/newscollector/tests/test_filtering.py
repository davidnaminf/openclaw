from datetime import datetime, timezone

from newscollector.filtering import dedupe_news, filter_by_topics, score_news
from newscollector.models import NewsItem


def _item(source: str, title: str, summary: str = "") -> NewsItem:
    return NewsItem(
        source=source,
        title=title,
        url="https://example.com",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        summary=summary,
    )


def test_dedupe_news():
    items = [
        _item("Reuters", "Fed holds rates steady"),
        _item("CNBC", "Fed holds rates steady!"),
    ]
    deduped = dedupe_news(items)
    assert len(deduped) == 1


def test_filter_by_topics():
    items = [
        _item("Reuters", "Company raises guidance", "Strong AI demand"),
        _item("Reuters", "Sports update", "No market impact"),
    ]
    filtered = filter_by_topics(items, ["ai", "guidance"])
    assert len(filtered) == 1


def test_score_news_order():
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    items = [
        _item("Reuters", "Fed signals policy pause", "inflation cools"),
        _item("CNBC", "Daily market wrap", "mixed session"),
    ]
    scored = score_news(items, ["fed", "inflation"], {"Reuters": 1.4, "CNBC": 1.0}, now)
    assert scored[0].source == "Reuters"


def test_filter_does_not_match_partial_word():
    items = [_item("CNBC", "CEO praises turnaround plan", "No AI details in article")]
    filtered = filter_by_topics(items, ["ai"])
    assert len(filtered) == 1

    items2 = [_item("CNBC", "CEO praises turnaround plan", "No model discussion")]
    filtered2 = filter_by_topics(items2, ["ai"])
    assert len(filtered2) == 0
