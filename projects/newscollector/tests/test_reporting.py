from datetime import datetime, timezone
from pathlib import Path

from newscollector.models import DailyReport, NewsItem, VideoItem
from newscollector.reporting import save_report


def test_save_report_writes_raw_text_artifacts(tmp_path: Path):
    report = DailyReport(
        report_date="2026-02-14",
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        headlines=[
            NewsItem(
                source="Reuters",
                title="Sample News",
                url="https://example.com/news",
                published_at=datetime(2026, 2, 14, tzinfo=timezone.utc),
                article_text="Full article body text.",
            )
        ],
        youtube=[
            VideoItem(
                channel_id="UC1",
                channel_title="Demo Channel",
                video_id="abc123",
                title="Sample Video",
                url="https://youtube.com/watch?v=abc123",
                published_at=datetime(2026, 2, 14, tzinfo=timezone.utc),
                transcript_text="Full transcript text.",
            )
        ],
    )

    json_path, md_path = save_report(report, tmp_path)
    assert json_path.exists()
    assert md_path.exists()
    assert report.headlines[0].article_text_file is not None
    assert report.youtube[0].transcript_file is not None
    assert (tmp_path / report.headlines[0].article_text_file).exists()
    assert (tmp_path / report.youtube[0].transcript_file).exists()


def test_save_report_cleans_stale_raw_text_files_on_same_date(tmp_path: Path):
    first = DailyReport(
        report_date="2026-02-14",
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        headlines=[
            NewsItem(
                source="Reuters",
                title="Old News",
                url="https://example.com/old",
                published_at=datetime(2026, 2, 14, tzinfo=timezone.utc),
                article_text="Old text.",
            ),
            NewsItem(
                source="Reuters",
                title="Another Old News",
                url="https://example.com/old2",
                published_at=datetime(2026, 2, 14, tzinfo=timezone.utc),
                article_text="Old text 2.",
            ),
        ],
        youtube=[
            VideoItem(
                channel_id="UC1",
                channel_title="Demo Channel",
                video_id="oldvid",
                title="Old Video",
                url="https://youtube.com/watch?v=oldvid",
                published_at=datetime(2026, 2, 14, tzinfo=timezone.utc),
                transcript_text="Old transcript.",
            )
        ],
    )
    save_report(first, tmp_path)

    second = DailyReport(
        report_date="2026-02-14",
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        headlines=[
            NewsItem(
                source="Reuters",
                title="New News",
                url="https://example.com/new",
                published_at=datetime(2026, 2, 14, tzinfo=timezone.utc),
                article_text="New text.",
            )
        ],
        youtube=[
            VideoItem(
                channel_id="UC1",
                channel_title="Demo Channel",
                video_id="newvid",
                title="New Video",
                url="https://youtube.com/watch?v=newvid",
                published_at=datetime(2026, 2, 14, tzinfo=timezone.utc),
                transcript_text="New transcript.",
            )
        ],
    )
    save_report(second, tmp_path)

    rss_files = list((tmp_path / "raw_text_20260214" / "rss").glob("*.txt"))
    yt_files = list((tmp_path / "raw_text_20260214" / "youtube").glob("*.txt"))
    assert len(rss_files) == 1
    assert len(yt_files) == 1
    assert "New_News" in rss_files[0].name
    assert "newvid" in yt_files[0].name


def test_save_report_mirrors_markdown_to_extra_directory(tmp_path: Path):
    report = DailyReport(
        report_date="2026-02-14",
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        headlines=[],
        youtube=[],
    )

    mirror_dir = tmp_path / "vault" / "raw_collection"
    _, md_path = save_report(report, tmp_path, mirror_md_dir=mirror_dir)
    mirrored_path = mirror_dir / md_path.name

    assert mirrored_path.exists()
    assert mirrored_path.read_text(encoding="utf-8") == md_path.read_text(encoding="utf-8")
