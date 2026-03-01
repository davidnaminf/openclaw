from datetime import datetime, timezone
from pathlib import Path

from newscollector.history_store import HistoryStore
from newscollector.models import DailyReport, NewsItem, VideoItem


def test_history_store_upsert_and_window_load(tmp_path: Path):
    db_path = tmp_path / "history.sqlite3"
    store = HistoryStore(db_path=db_path)
    store.initialize()

    now = datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc)
    report = DailyReport(
        report_date="2026-02-14",
        generated_at=now.isoformat(),
        headlines=[
            NewsItem(
                source="CNBC",
                title="Sample RSS",
                url="https://example.com/rss1",
                published_at=now,
                article_text="Body text",
                summary="Short summary",
            )
        ],
        youtube=[
            VideoItem(
                channel_id="UC1",
                channel_title="Demo Channel",
                video_id="vid-1",
                title="Sample Video",
                url="https://youtube.com/watch?v=vid-1",
                published_at=now,
                transcript_text="Transcript",
            )
        ],
        macro=[
            NewsItem(
                source="Weather/Seoul",
                title="Seoul weather forecast (2026-02-14)",
                url="https://api.open-meteo.com",
                published_at=now,
                summary="Clear, min 0C, max 8C.",
            )
        ],
    )

    assert store.has_any_items() is False
    store.upsert_report(report)
    assert store.has_any_items() is True
    # Reinsert same report to verify upsert + dedupe.
    store.upsert_report(report)

    window = store.load_window(end_utc=now, days=7)
    assert len(window.headlines) == 1
    assert len(window.youtube) == 1
    assert len(window.macro) == 1
    assert window.headlines[0].url == "https://example.com/rss1"
    assert window.youtube[0].video_id == "vid-1"
