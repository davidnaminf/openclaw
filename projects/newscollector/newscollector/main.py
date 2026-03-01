from __future__ import annotations

import argparse
import concurrent.futures
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from newscollector.config import ROOT_DIR, Settings, SourceConfig
from newscollector.filtering import dedupe_news, filter_by_topics, score_news
from newscollector.history_store import HistoryStore, HistoryWindow
from newscollector.ingestion.article_text import fetch_article_text
from newscollector.ingestion.context import fetch_upcoming_holidays, fetch_weather_snapshot
from newscollector.ingestion.rss import fetch_rss_items
from newscollector.ingestion.youtube import fetch_recent_videos
from newscollector.ingestion.youtube_subtitles import fetch_video_transcript
from newscollector.models import DailyReport, NewsItem, VideoItem
from newscollector.reporting import save_report, to_markdown
from newscollector.weekly_summary import render_weekly_summary_report


def _matches_token(text: str, token: str) -> bool:
    token = token.lower().strip()
    if not token:
        return False
    if " " in token:
        return token in text
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def _apply_source_filter(source_id: str, include: set[str], exclude: set[str]) -> bool:
    if source_id in exclude:
        return False
    if include and source_id not in include:
        return False
    return True


def _filter_videos_by_topics(videos: list[VideoItem], topics: list[str]) -> list[VideoItem]:
    checks = [topic.lower().strip() for topic in topics if topic.strip()]
    if not checks:
        return videos

    filtered: list[VideoItem] = []
    for video in videos:
        text = (
            f"{video.title} {video.description} {video.transcript_text}".lower().strip()
        )
        if any(_matches_token(text, token) for token in checks):
            filtered.append(video)
    return filtered


def _extract_youtube_originals(
    settings: Settings,
    cutoff_utc: datetime,
    source_by_channel_id: dict[str, SourceConfig],
) -> list[VideoItem]:
    if not source_by_channel_id:
        return []
    if not settings.youtube_api_key:
        print("[WARN] YOUTUBE_API_KEY is missing; skipped YouTube collection.")
        return []

    channel_ids = list(source_by_channel_id.keys())
    max_per_channel = max(1, settings.filters.max_videos // max(len(channel_ids), 1))
    videos = fetch_recent_videos(
        api_key=settings.youtube_api_key,
        channel_ids=channel_ids,
        published_after_utc=cutoff_utc,
        max_per_channel=max_per_channel,
    )

    for video in videos:
        source_cfg = source_by_channel_id.get(video.channel_id)
        if source_cfg:
            # Keep user-configured source name visible in report/filter.
            video.channel_title = source_cfg.name

        if not source_cfg or not source_cfg.extract_subtitles:
            continue

        subtitle_dir = Path(settings.subtitle_dir) / (
            source_cfg.id if source_cfg else (video.channel_id or video.channel_title)
        )
        preferred_langs = source_cfg.subtitle_langs or settings.default_subtitle_langs
        subtitle_result = fetch_video_transcript(
            yt_dlp=settings.yt_dlp_path,
            video_url=video.url,
            output_dir=subtitle_dir,
            preferred_langs=preferred_langs,
            command_timeout_seconds=settings.subtitle_command_timeout_seconds,
        )
        if subtitle_result.has_subtitles and subtitle_result.transcript_text:
            video.subtitle_lang = subtitle_result.subtitle_lang
            video.subtitle_type = subtitle_result.subtitle_type
            video.transcript_text = subtitle_result.transcript_text
        elif subtitle_result.error:
            print(
                f"[WARN] Subtitle extraction skipped for {video.video_id}: "
                f"{subtitle_result.error}"
            )

    return videos


def _enrich_rss_with_article_text(
    items: list[NewsItem],
    workers: int,
    timeout_seconds: int,
    max_retries: int,
) -> None:
    if not items:
        return

    def _one(item: NewsItem) -> tuple[NewsItem, str, str | None]:
        result = fetch_article_text(
            item.url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        return item, result.text, result.error

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(_one, item) for item in items]
        for future in concurrent.futures.as_completed(futures):
            item, text, error = future.result()
            if text:
                item.article_text = text
            elif error:
                print(f"[WARN] Article fetch skipped for {item.url}: {error}")


def _format_num(value: float | None, unit: str, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}{unit}"


def _weather_icon(weather_code: int | None) -> str:
    if weather_code is None:
        return "🌤️"
    if weather_code in {0, 1}:
        return "☀️"
    if weather_code in {2, 3, 45, 48}:
        return "☁️"
    if weather_code in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        return "🌧️"
    if weather_code in {71, 73, 75, 77, 85, 86}:
        return "❄️"
    if weather_code in {95, 96, 99}:
        return "⛈️"
    return "🌤️"


def _collect_macro_items(settings: Settings, now_utc: datetime) -> list[NewsItem]:
    macro_items: list[NewsItem] = []

    if settings.weather_context.enabled:
        snapshot, error = fetch_weather_snapshot(
            location_name=settings.weather_context.location_name,
            latitude=settings.weather_context.latitude,
            longitude=settings.weather_context.longitude,
            timezone=settings.weather_context.timezone,
            forecast_days=settings.weather_context.forecast_days,
        )
        if error:
            print(f"[WARN] Weather context skipped: {error}")
        if snapshot:
            title = (
                f"{snapshot.location_name} weather forecast "
                f"({snapshot.forecast_date.isoformat()})"
            )
            icon = _weather_icon(snapshot.weather_code)
            summary = (
                f"{icon} {snapshot.weather_text}, "
                f"min {_format_num(snapshot.temp_min_c, 'C')}, "
                f"max {_format_num(snapshot.temp_max_c, 'C')}, "
                f"precip prob {_format_num(snapshot.precipitation_probability_max, '%', 0)}, "
                f"precip sum {_format_num(snapshot.precipitation_sum_mm, 'mm')}."
            )
            macro_items.append(
                NewsItem(
                    source=f"Weather/{snapshot.location_name}",
                    title=title,
                    url=snapshot.source_url,
                    published_at=now_utc,
                    summary=summary,
                    why_it_matters=(
                        "Weather can affect local mobility, retail demand, and short-term "
                        "power usage in Korea-linked sectors."
                    ),
                )
            )

    if settings.holiday_context.enabled:
        holiday_window, error = fetch_upcoming_holidays(
            country_codes=settings.holiday_context.country_codes,
            window_days=settings.holiday_context.window_days,
            reference_date=now_utc.astimezone(ZoneInfo("Asia/Seoul")).date(),
            manual_ranges=settings.holiday_context.manual_ranges,
        )
        if error:
            print(f"[WARN] Holiday context partial errors: {error}")

        for country_code in settings.holiday_context.country_codes:
            country = country_code.upper()
            events = holiday_window.by_country.get(country, [])
            title = (
                f"{country} public holidays "
                f"({holiday_window.start_date.isoformat()} ~ "
                f"{holiday_window.end_date.isoformat()})"
            )
            if events:
                pieces = []
                for event in events[:5]:
                    local_suffix = (
                        f" / {event.local_name}"
                        if event.local_name and event.local_name != event.name
                        else ""
                    )
                    pieces.append(
                        f"{event.date.isoformat()} {event.name}{local_suffix}"
                    )
                if len(events) > 5:
                    pieces.append(f"+{len(events) - 5} more")
                summary = "; ".join(pieces)
            else:
                summary = (
                    "No public holidays in the selected window."
                )
            macro_items.append(
                NewsItem(
                    source=f"Holiday/{country}",
                    title=title,
                    url="https://date.nager.at/Api",
                    published_at=now_utc,
                    summary=summary,
                    why_it_matters=(
                        "Public holidays can reduce regional trading liquidity and "
                        "shift operational cadence for supply chains."
                    ),
                )
            )

    return macro_items


def run(output_dir: Path, config_override: str | None = None) -> int:
    settings = Settings.from_env(config_override=config_override)
    now_utc = datetime.now(tz=timezone.utc)
    local_date = now_utc.astimezone(ZoneInfo(settings.report_timezone)).date()
    kr_date = now_utc.astimezone(ZoneInfo("Asia/Seoul")).date()

    history_store: HistoryStore | None = None
    effective_lookback_hours = settings.lookback_hours
    if settings.history.enabled:
        db_path = Path(settings.history.db_path)
        if not db_path.is_absolute():
            db_path = ROOT_DIR / db_path
        history_store = HistoryStore(db_path=db_path)
        history_store.initialize()
        if not history_store.has_any_items():
            effective_lookback_hours = max(
                effective_lookback_hours,
                settings.history.bootstrap_lookback_days * 24,
            )
            print(
                "[INFO] History DB is empty; bootstrap lookback set to "
                f"{effective_lookback_hours} hours."
            )
    cutoff_utc = now_utc - timedelta(hours=effective_lookback_hours)

    include_sources = set(settings.filters.include_sources)
    exclude_sources = set(settings.filters.exclude_sources)

    rss_source_map = {
        source.name: source.url
        for source in settings.rss_sources()
        if _apply_source_filter(source.id, include_sources, exclude_sources)
        and source.url
    }
    rss_items = fetch_rss_items(rss_source_map, cutoff_utc=cutoff_utc)

    if settings.filters.enabled:
        rss_items = filter_by_topics(rss_items, settings.filters.topic_keywords)
        if settings.filters.dedupe_titles:
            rss_items = dedupe_news(rss_items)
        rss_items = score_news(
            rss_items,
            topics=settings.filters.topic_keywords,
            source_weights=settings.filters.source_weights,
            now_utc=now_utc,
        )
        if settings.filters.min_score > 0:
            rss_items = [
                item for item in rss_items if item.score >= settings.filters.min_score
            ]
    else:
        rss_items.sort(key=lambda x: x.published_at, reverse=True)

    rss_items = rss_items[: settings.filters.max_news_items]
    if settings.fetch_article_text:
        _enrich_rss_with_article_text(
            rss_items,
            workers=settings.article_fetch_workers,
            timeout_seconds=settings.article_fetch_timeout_seconds,
            max_retries=settings.article_fetch_retries,
        )

    raw_youtube_sources = {
        source.channel_id: source
        for source in settings.youtube_sources()
        if source.channel_id
        and _apply_source_filter(source.id, include_sources, exclude_sources)
    }
    videos = _extract_youtube_originals(settings, cutoff_utc, raw_youtube_sources)

    if settings.filters.enabled and settings.filters.apply_topic_filter_to_youtube:
        videos = _filter_videos_by_topics(videos, settings.filters.topic_keywords)

    seen_video_ids: set[str] = set()
    deduped_videos: list[VideoItem] = []
    for video in sorted(videos, key=lambda x: x.published_at, reverse=True):
        if video.video_id in seen_video_ids:
            continue
        seen_video_ids.add(video.video_id)
        deduped_videos.append(video)
    videos = deduped_videos[: settings.filters.max_videos]
    macro_items = _collect_macro_items(settings, now_utc)

    report = DailyReport(
        report_date=local_date.isoformat(),
        generated_at=now_utc.isoformat(),
        headlines=rss_items,
        youtube=videos,
        macro=macro_items,
    )

    mirror_md_dir: Path | None = None
    if settings.raw_collection_copy_dir:
        mirror_md_dir = Path(settings.raw_collection_copy_dir)
        if not mirror_md_dir.is_absolute():
            mirror_md_dir = ROOT_DIR / mirror_md_dir

    _, md_path = save_report(
        report,
        output_dir=output_dir,
        mirror_md_dir=mirror_md_dir,
    )
    raw_collection_md_path_for_summary: Path | None = None
    if mirror_md_dir is not None:
        raw_collection_md_path_for_summary = mirror_md_dir / md_path.name
    if mirror_md_dir is not None:
        print(f"[INFO] Raw collection markdown copied to {mirror_md_dir / md_path.name}")
    if history_store is not None:
        history_store.upsert_report(report)
        print(f"[INFO] History DB updated: {history_store.db_path}")

    if settings.weekly_summary.enabled:
        template_path = Path(settings.weekly_summary.template_path)
        if not template_path.is_absolute():
            template_path = ROOT_DIR / template_path

        if not template_path.exists():
            print(
                "[WARN] Weekly summary template not found, skipped: "
                f"{template_path}"
            )
        else:
            if history_store is not None:
                window = history_store.load_window(
                    end_utc=now_utc,
                    days=settings.history.weekly_window_days,
                )
            else:
                window = HistoryWindow(
                    start_utc=now_utc - timedelta(days=7),
                    end_utc=now_utc,
                    headlines=report.headlines,
                    youtube=report.youtube,
                    macro=report.macro,
                )

            summary_output_dir = Path(settings.weekly_summary.output_dir)
            if not summary_output_dir.is_absolute():
                summary_output_dir = ROOT_DIR / summary_output_dir
            summary_path, mode = render_weekly_summary_report(
                template_path=template_path,
                output_dir=summary_output_dir,
                window=window,
                report_date=local_date,
                holiday_reference_date=kr_date,
                openai_api_key=settings.openai_api_key,
                model=settings.weekly_summary.model,
                max_rss_items=settings.weekly_summary.max_rss_items,
                max_youtube_items=settings.weekly_summary.max_youtube_items,
                raw_collection_md_path=raw_collection_md_path_for_summary,
                generated_at=now_utc,
                rss_quality_dir=ROOT_DIR / "output" / "rss_quality",
            )
            print(
                "[INFO] Weekly summary saved at "
                f"{summary_path} (mode={mode}, window_days="
                f"{settings.history.weekly_window_days})"
            )

    print(to_markdown(report))
    print(f"\n[INFO] Raw collection saved at {md_path}")
    print(f"[INFO] Config file: {settings.config_file}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect original items from RSS and YouTube sources."
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for collection artifacts (md/json).",
    )
    parser.add_argument(
        "--config-file",
        default=None,
        help="Override pipeline config path (default: PIPELINE_CONFIG_FILE or config/pipeline.json).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    raise SystemExit(run(output_dir=Path(args.output_dir), config_override=args.config_file))
