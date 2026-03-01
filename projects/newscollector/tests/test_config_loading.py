import json
from pathlib import Path

from newscollector.config import Settings


def test_settings_loads_pipeline_config(monkeypatch, tmp_path: Path):
    config_file = tmp_path / "pipeline.json"
    config_file.write_text(
        json.dumps(
            {
                "report_timezone": "America/New_York",
                "lookback_hours": 12,
                "runtime": {
                    "subtitle_dir": "subtitles",
                    "yt_dlp_path": "yt-dlp",
                    "subtitle_command_timeout_seconds": 17,
                    "raw_collection_copy_dir": "vault/raw_collection",
                    "default_subtitle_langs": ["ko", "en"],
                },
                "filters": {
                    "enabled": True,
                    "topic_keywords": ["ai"],
                    "max_news_items": 5,
                    "max_videos": 3,
                },
                "context_sources": {
                    "weather": {
                        "enabled": True,
                        "location_name": "Seoul",
                        "latitude": 37.5665,
                        "longitude": 126.9780,
                        "timezone": "Asia/Seoul",
                        "forecast_days": 1,
                    },
                    "holidays": {
                        "enabled": True,
                        "country_codes": ["KR", "US"],
                        "window_days": 5,
                        "manual_ranges": {
                            "CN": [
                                {
                                    "start": "2026-02-15",
                                    "end": "2026-02-23",
                                    "name": "Chinese New Year (Spring Festival)",
                                    "local_name": "春节",
                                }
                            ]
                        },
                    },
                },
                "history": {
                    "enabled": True,
                    "db_path": "output/history/test.sqlite3",
                    "bootstrap_lookback_days": 7,
                    "weekly_window_days": 7,
                },
                "weekly_summary": {
                    "enabled": True,
                    "template_path": "template.md",
                    "output_dir": "output/weekly_reports",
                    "model": "gpt-5-mini",
                    "max_rss_items": 12,
                    "max_youtube_items": 9,
                },
                "sources": [
                    {
                        "id": "r1",
                        "type": "rss",
                        "name": "Reuters",
                        "url": "https://example.com/rss.xml",
                        "enabled": True,
                    },
                    {
                        "id": "y1",
                        "type": "youtube_channel",
                        "name": "Demo",
                        "channel_id": "UC123",
                        "enabled": True,
                        "extract_subtitles": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PIPELINE_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("YOUTUBE_API_KEY", "abc")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-xyz")

    settings = Settings.from_env()

    assert settings.lookback_hours == 12
    assert settings.filters.topic_keywords == ["ai"]
    assert len(settings.rss_sources()) == 1
    assert len(settings.youtube_sources()) == 1
    assert settings.youtube_api_key == "abc"
    assert settings.weather_context.enabled is True
    assert settings.weather_context.location_name == "Seoul"
    assert settings.holiday_context.enabled is True
    assert settings.holiday_context.country_codes == ["KR", "US"]
    assert settings.holiday_context.window_days == 5
    assert settings.holiday_context.manual_ranges["CN"][0]["start"] == "2026-02-15"
    assert settings.history.enabled is True
    assert settings.history.db_path == "output/history/test.sqlite3"
    assert settings.history.bootstrap_lookback_days == 7
    assert settings.history.weekly_window_days == 7
    assert settings.weekly_summary.enabled is True
    assert settings.weekly_summary.template_path == "template.md"
    assert settings.weekly_summary.model == "gpt-5-mini"
    assert settings.weekly_summary.max_rss_items == 12
    assert settings.weekly_summary.max_youtube_items == 9
    assert settings.raw_collection_copy_dir == "vault/raw_collection"
    assert settings.subtitle_command_timeout_seconds == 17
    assert settings.openai_api_key == "openai-xyz"


def test_settings_topic_keywords_explicit_empty_list_stays_empty(
    monkeypatch, tmp_path: Path
):
    config_file = tmp_path / "pipeline-empty-topics.json"
    config_file.write_text(
        json.dumps(
            {
                "report_timezone": "Asia/Seoul",
                "lookback_hours": 24,
                "filters": {
                    "enabled": True,
                    "topic_keywords": [],
                },
                "sources": [
                    {
                        "id": "r1",
                        "type": "rss",
                        "name": "Reuters",
                        "url": "https://example.com/rss.xml",
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PIPELINE_CONFIG_FILE", str(config_file))

    settings = Settings.from_env()
    assert settings.filters.topic_keywords == []


def test_settings_topic_keywords_uses_default_when_missing(
    monkeypatch, tmp_path: Path
):
    config_file = tmp_path / "pipeline-no-topics.json"
    config_file.write_text(
        json.dumps(
            {
                "report_timezone": "Asia/Seoul",
                "lookback_hours": 24,
                "filters": {
                    "enabled": True,
                },
                "sources": [
                    {
                        "id": "r1",
                        "type": "rss",
                        "name": "Reuters",
                        "url": "https://example.com/rss.xml",
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PIPELINE_CONFIG_FILE", str(config_file))

    settings = Settings.from_env()
    assert settings.filters.topic_keywords != []
