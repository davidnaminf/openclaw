import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import newscollector.weekly_summary as weekly_summary
from newscollector.history_store import HistoryWindow
from newscollector.models import NewsItem, VideoItem
from newscollector.weekly_summary import _extract_today_holidays, render_weekly_summary_report


def test_render_weekly_summary_report_strips_comments_and_fills_frontmatter(tmp_path: Path):
    template = tmp_path / "template.md"
    template.write_text(
        (
            "---\n"
            "Date: \n"
            "Main topic: % comment\n"
            "Whether: % comment\n"
            "Holyday : % comment\n"
            "tags: []\n"
            "---\n"
            "# Dummy Section\n"
            "% hidden\n"
        ),
        encoding="utf-8",
    )

    now = datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc)
    window = HistoryWindow(
        start_utc=now,
        end_utc=now,
        headlines=[
            NewsItem(
                source="CNBC",
                title="Inflation cools",
                url="https://example.com/a",
                published_at=now,
                summary="Inflation data was softer than expected.",
            )
        ],
        youtube=[
            VideoItem(
                channel_id="UC1",
                channel_title="Demo",
                video_id="v1",
                title="AI outlook",
                url="https://youtube.com/watch?v=v1",
                published_at=now,
                description="Discussion on AI and rates.",
            )
        ],
        macro=[
            NewsItem(
                source="Weather/Seoul",
                title="Seoul weather forecast (2026-02-14)",
                url="https://api.open-meteo.com",
                published_at=now,
                summary="Overcast, min 0C, max 8C.",
            ),
            NewsItem(
                source="Holiday/US",
                title="US public holidays",
                url="https://date.nager.at/Api",
                published_at=now,
                summary="2026-02-14 Test Holiday",
            ),
        ],
    )

    out_path, mode = render_weekly_summary_report(
        template_path=template,
        output_dir=tmp_path / "out",
        window=window,
        report_date=date(2026, 2, 14),
        holiday_reference_date=date(2026, 2, 14),
        openai_api_key=None,
        model="gpt-5-mini",
        max_rss_items=20,
        max_youtube_items=20,
        raw_collection_md_path=tmp_path / "out" / "raw_collection" / "raw_collection_20260214.md",
        generated_at=now,
        rss_quality_dir=tmp_path / "rss_quality",
    )

    assert mode == "heuristic"
    text = out_path.read_text(encoding="utf-8")
    assert "% hidden" not in text
    assert "Date: 2026-02-14" in text
    assert 'Main topic: "' in text
    assert 'Whether: "' in text
    assert 'Holyday: "🇺🇸 US: Test Holiday"' in text
    assert 'tags: ["daily-summary", "weekly-window", "trend-first"]' in text
    assert 'Generated at: "2026-02-14T12:00:00+00:00"' in text
    assert 'Summary mode: "heuristic"' in text
    assert 'Summary model: "gpt-5-mini"' in text
    assert "LLM prompt tokens: 0" in text
    assert "LLM completion tokens: 0" in text
    assert "LLM total tokens: 0" in text
    assert "LLM cached prompt tokens: 0" in text
    assert "LLM estimated cost (USD): 0.00000000" in text
    assert "Window start: 2026-02-14" in text
    assert "Window end: 2026-02-14" in text
    assert "Window days: 1" in text
    assert "RSS count: 1" in text
    assert "YouTube count: 1" in text
    assert "Macro count: 2" in text
    assert 'RSS quality: "useful 100.0% (1/1), one-off 0.0% (0/1)"' in text
    assert 'Raw collection: "[[raw_collection/raw_collection_20260214]]"' in text
    assert "# 뉴스 종합" in text
    assert "## 1." not in text
    assert "## 한국" in text
    assert "## 미국/글로벌" in text
    assert "- **삼성전자**:" in text
    assert "- **Nvidia**:" in text
    assert "# RSS 소스 평가" in text
    assert "# Dummy Section" not in text
    assert "Related files:" not in text
    assert (tmp_path / "rss_quality" / "rss_quality_20260214.json").exists()
    assert (tmp_path / "rss_quality" / "rss_quality_202602_summary.json").exists()


def test_render_weekly_summary_report_includes_openai_usage_and_cost(
    tmp_path: Path,
    monkeypatch,
):
    template = tmp_path / "template.md"
    template.write_text(
        (
            "---\n"
            "Date: \n"
            "Main topic: \n"
            "Whether: \n"
            "Holiday: \n"
            "tags: []\n"
            "---\n"
        ),
        encoding="utf-8",
    )

    completion_payload = json.dumps(
        {
            "main_topic": "테스트 메인 주제",
            "tags": ["tag-a", "tag-b"],
            "report_body": (
                "# 뉴스 종합\n"
                "- 테스트\n\n"
                "# 한국 주식 시장 전망\n"
                "- 테스트\n\n"
                "# 미국 주식 시장 전망\n"
                "- 테스트\n\n"
                "# 개별 주 주식 시장 전망\n"
                "## 한국\n"
                "- 삼성전자: 테스트\n"
                "## 미국/글로벌\n"
                "- Nvidia: 테스트\n\n"
                "# AI 의견\n"
                "- 테스트"
            ),
        },
        ensure_ascii=False,
    )

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                model="gpt-5-mini",
                choices=[SimpleNamespace(message=SimpleNamespace(content=completion_payload))],
                usage=SimpleNamespace(
                    prompt_tokens=2000,
                    completion_tokens=500,
                    total_tokens=2500,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=0),
                ),
            )

    class FakeOpenAI:
        def __init__(self, api_key: str):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(weekly_summary, "OpenAI", FakeOpenAI)

    now = datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc)
    window = HistoryWindow(
        start_utc=now,
        end_utc=now,
        headlines=[],
        youtube=[],
        macro=[],
    )

    out_path, mode = render_weekly_summary_report(
        template_path=template,
        output_dir=tmp_path / "out",
        window=window,
        report_date=date(2026, 2, 14),
        holiday_reference_date=date(2026, 2, 14),
        openai_api_key="test-key",
        model="gpt-5-mini",
        max_rss_items=20,
        max_youtube_items=20,
        generated_at=now,
    )

    text = out_path.read_text(encoding="utf-8")
    assert mode == "openai"
    assert 'Summary mode: "openai"' in text
    assert 'Summary model: "gpt-5-mini"' in text
    assert "LLM prompt tokens: 2000" in text
    assert "LLM completion tokens: 500" in text
    assert "LLM total tokens: 2500" in text
    assert "LLM cached prompt tokens: 0" in text
    assert "LLM estimated cost (USD): 0.00150000" in text


def test_extract_today_holidays_dedupes_rows():
    macro_lines = [
        "Holiday/KR | 2026-02-16 Lunar New Year",
        "Holiday/KR | 2026-02-16 Lunar New Year",
        "Holiday/US | 2026-02-16 Presidents Day",
        "Holiday/US | 2026-02-16 Presidents Day",
        "Holiday/US | 2026-02-17 Other Day",
    ]

    line = _extract_today_holidays(macro_lines, today=date(2026, 2, 16))
    assert line == "🇰🇷 KR: Lunar New Year; 🇺🇸 US: Presidents Day"
