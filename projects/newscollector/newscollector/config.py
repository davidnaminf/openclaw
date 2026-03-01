from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PIPELINE_FILE = ROOT_DIR / "config" / "pipeline.json"

DEFAULT_TOPICS = [
    "rate",
    "inflation",
    "fed",
    "ai",
    "semiconductor",
    "earnings",
    "guidance",
    "gpu",
    "geopolitical",
    "korea",
    "nasdaq",
    "ionq",
    "trump",
    "stock",
    "money",
    "software",
    "sw",
    "tesla",
    "robot",
    "driving",
    "ADAS",
    "autonomous",
    "self-driving",
    "infineon",
    "giant"

]

DEFAULT_SOURCE_WEIGHTS = {
    "Reuters": 1.4,
    "Bloomberg": 1.3,
    "Financial Times": 1.2,
    "CNBC": 1.0,
    "Wall Street Journal": 1.1,
    "Federal Reserve": 1.6,
    "BLS": 1.6,
    "Semiconductor Engineering": 1.0,
    "EE Times": 1.0,
}


def _to_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _str_list(value: object) -> List[str]:
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    return []


def _float_map(value: object, fallback: Dict[str, float]) -> Dict[str, float]:
    out = fallback.copy()
    if not isinstance(value, dict):
        return out
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        try:
            out[key.strip()] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


def _to_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _manual_holiday_ranges(value: object) -> Dict[str, List[Dict[str, str]]]:
    out: Dict[str, List[Dict[str, str]]] = {}
    if not isinstance(value, dict):
        return out

    for raw_country, raw_rows in value.items():
        country = str(raw_country).upper().strip()
        if len(country) != 2 or not isinstance(raw_rows, list):
            continue

        rows: List[Dict[str, str]] = []
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue

            start = str(raw.get("start", "")).strip()
            end = str(raw.get("end", "")).strip() or start
            name = str(raw.get("name", "")).strip()
            local_name = str(raw.get("local_name", "")).strip()
            if not start:
                continue
            try:
                start_date = date.fromisoformat(start)
                end_date = date.fromisoformat(end)
            except ValueError:
                continue
            if end_date < start_date:
                continue
            if not name and not local_name:
                continue

            rows.append(
                {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat(),
                    "name": name,
                    "local_name": local_name,
                }
            )

        if rows:
            out[country] = rows
    return out


@dataclass
class SourceConfig:
    id: str
    type: str
    name: str
    enabled: bool = True
    url: str | None = None
    channel_id: str | None = None
    extract_subtitles: bool = False
    subtitle_langs: List[str] = field(default_factory=list)


@dataclass
class FilterConfig:
    enabled: bool = True
    topic_keywords: List[str] = field(default_factory=lambda: DEFAULT_TOPICS.copy())
    dedupe_titles: bool = True
    include_sources: List[str] = field(default_factory=list)
    exclude_sources: List[str] = field(default_factory=list)
    source_weights: Dict[str, float] = field(
        default_factory=lambda: DEFAULT_SOURCE_WEIGHTS.copy()
    )
    min_score: float = 0.0
    max_news_items: int = 20
    max_videos: int = 10
    apply_topic_filter_to_youtube: bool = True


@dataclass
class WeatherContextConfig:
    enabled: bool = False
    provider: str = "open_meteo"
    location_name: str = "Seoul"
    latitude: float = 37.5665
    longitude: float = 126.9780
    timezone: str = "Asia/Seoul"
    forecast_days: int = 1


@dataclass
class HolidayContextConfig:
    enabled: bool = False
    provider: str = "nager_date"
    country_codes: List[str] = field(
        default_factory=lambda: ["KR", "US", "DE", "AT", "CN"]
    )
    window_days: int = 7
    manual_ranges: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)


@dataclass
class HistoryConfig:
    enabled: bool = True
    db_path: str = "output/history/newscollector.sqlite3"
    bootstrap_lookback_days: int = 7
    weekly_window_days: int = 7


@dataclass
class WeeklySummaryConfig:
    enabled: bool = True
    template_path: str = ""
    output_dir: str = "output/weekly_reports"
    model: str = "gpt-5-mini"
    max_rss_items: int = 60
    max_youtube_items: int = 40


@dataclass
class Settings:
    report_timezone: str
    lookback_hours: int
    fetch_article_text: bool
    article_fetch_workers: int
    article_fetch_timeout_seconds: int
    article_fetch_retries: int
    subtitle_dir: str
    yt_dlp_path: str
    subtitle_command_timeout_seconds: int
    raw_collection_copy_dir: str
    default_subtitle_langs: List[str]
    filters: FilterConfig
    sources: List[SourceConfig]
    weather_context: WeatherContextConfig
    holiday_context: HolidayContextConfig
    history: HistoryConfig
    weekly_summary: WeeklySummaryConfig
    youtube_api_key: str | None
    openai_api_key: str | None
    config_file: Path

    @classmethod
    def from_env(cls, config_override: str | None = None) -> "Settings":
        load_dotenv(ROOT_DIR / ".env")

        config_file = config_override or os.getenv(
            "PIPELINE_CONFIG_FILE", str(DEFAULT_PIPELINE_FILE)
        )
        config_path = Path(config_file)
        if not config_path.is_absolute():
            config_path = ROOT_DIR / config_path
        if not config_path.exists():
            raise FileNotFoundError(
                f"Pipeline config not found: {config_path}. "
                "Create it from config/pipeline.json."
            )

        with config_path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)

        if not isinstance(data, dict):
            raise ValueError("Pipeline config must be a JSON object.")

        runtime = data.get("runtime", {})
        if not isinstance(runtime, dict):
            runtime = {}

        filters_data = data.get("filters", {})
        if not isinstance(filters_data, dict):
            filters_data = {}
        context_data = data.get("context_sources", {})
        if not isinstance(context_data, dict):
            context_data = {}
        history_data = data.get("history", {})
        if not isinstance(history_data, dict):
            history_data = {}
        weekly_summary_data = data.get("weekly_summary", {})
        if not isinstance(weekly_summary_data, dict):
            weekly_summary_data = {}
        weather_data = context_data.get("weather", {})
        if not isinstance(weather_data, dict):
            weather_data = {}
        holiday_data = context_data.get("holidays", {})
        if not isinstance(holiday_data, dict):
            holiday_data = {}

        raw_sources = data.get("sources", [])
        if not isinstance(raw_sources, list):
            raw_sources = []

        sources: List[SourceConfig] = []
        for item in raw_sources:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("id", "")).strip()
            source_type = str(item.get("type", "")).strip().lower()
            source_name = str(item.get("name", "")).strip()
            if not source_id or not source_type or not source_name:
                continue
            if source_type not in {"rss", "youtube_channel"}:
                raise ValueError(
                    f"Unsupported source type '{source_type}' in source '{source_id}'. "
                    "Use 'rss' or 'youtube_channel'."
                )

            source = SourceConfig(
                id=source_id,
                type=source_type,
                name=source_name,
                enabled=_to_bool(item.get("enabled"), default=True),
                url=str(item.get("url", "")).strip() or None,
                channel_id=str(item.get("channel_id", "")).strip() or None,
                extract_subtitles=_to_bool(
                    item.get("extract_subtitles"),
                    default=(source_type == "youtube_channel"),
                ),
                subtitle_langs=_str_list(item.get("subtitle_langs")),
            )
            if source.type == "rss" and not source.url:
                raise ValueError(f"RSS source '{source.id}' requires 'url'.")
            if source.type == "youtube_channel" and not source.channel_id:
                raise ValueError(
                    f"YouTube source '{source.id}' requires 'channel_id'."
                )
            sources.append(source)

        if "topic_keywords" in filters_data:
            topic_keywords = _str_list(filters_data.get("topic_keywords"))
        else:
            topic_keywords = DEFAULT_TOPICS.copy()

        filters = FilterConfig(
            enabled=_to_bool(filters_data.get("enabled"), default=True),
            topic_keywords=topic_keywords,
            dedupe_titles=_to_bool(filters_data.get("dedupe_titles"), default=True),
            include_sources=_str_list(filters_data.get("include_sources")),
            exclude_sources=_str_list(filters_data.get("exclude_sources")),
            source_weights=_float_map(
                filters_data.get("source_weights"), DEFAULT_SOURCE_WEIGHTS
            ),
            min_score=float(filters_data.get("min_score", 0.0) or 0.0),
            max_news_items=int(filters_data.get("max_news_items", 20) or 20),
            max_videos=int(filters_data.get("max_videos", 10) or 10),
            apply_topic_filter_to_youtube=_to_bool(
                filters_data.get("apply_topic_filter_to_youtube"), default=True
            ),
        )

        weather_context = WeatherContextConfig(
            enabled=_to_bool(weather_data.get("enabled"), default=False),
            provider=str(weather_data.get("provider", "open_meteo")).strip()
            or "open_meteo",
            location_name=str(weather_data.get("location_name", "Seoul")).strip()
            or "Seoul",
            latitude=_to_float(weather_data.get("latitude", 37.5665), default=37.5665),
            longitude=_to_float(
                weather_data.get("longitude", 126.9780), default=126.9780
            ),
            timezone=str(weather_data.get("timezone", "Asia/Seoul")).strip()
            or "Asia/Seoul",
            forecast_days=max(
                1, min(16, _to_int(weather_data.get("forecast_days", 1), default=1))
            ),
        )
        holiday_context = HolidayContextConfig(
            enabled=_to_bool(holiday_data.get("enabled"), default=False),
            provider=str(holiday_data.get("provider", "nager_date")).strip()
            or "nager_date",
            country_codes=[
                code.upper()
                for code in _str_list(holiday_data.get("country_codes"))
                if len(code.strip()) == 2
            ]
            or ["KR", "US", "DE", "AT", "CN"],
            window_days=max(
                0, min(30, _to_int(holiday_data.get("window_days", 7), default=7))
            ),
            manual_ranges=_manual_holiday_ranges(holiday_data.get("manual_ranges")),
        )
        history = HistoryConfig(
            enabled=_to_bool(history_data.get("enabled"), default=True),
            db_path=str(
                history_data.get("db_path", "output/history/newscollector.sqlite3")
            ).strip()
            or "output/history/newscollector.sqlite3",
            bootstrap_lookback_days=max(
                1,
                min(
                    30,
                    _to_int(
                        history_data.get("bootstrap_lookback_days", 7),
                        default=7,
                    ),
                ),
            ),
            weekly_window_days=max(
                1,
                min(
                    30,
                    _to_int(history_data.get("weekly_window_days", 7), default=7),
                ),
            ),
        )
        weekly_summary = WeeklySummaryConfig(
            enabled=_to_bool(weekly_summary_data.get("enabled"), default=True),
            template_path=str(weekly_summary_data.get("template_path", "")).strip(),
            output_dir=str(
                weekly_summary_data.get("output_dir", "output/weekly_reports")
            ).strip()
            or "output/weekly_reports",
            model=str(weekly_summary_data.get("model", "gpt-5-mini")).strip()
            or "gpt-5-mini",
            max_rss_items=max(
                1,
                min(
                    200,
                    _to_int(weekly_summary_data.get("max_rss_items", 60), default=60),
                ),
            ),
            max_youtube_items=max(
                1,
                min(
                    200,
                    _to_int(
                        weekly_summary_data.get("max_youtube_items", 40),
                        default=40,
                    ),
                ),
            ),
        )

        default_subtitle_langs = _str_list(runtime.get("default_subtitle_langs"))
        if not default_subtitle_langs:
            default_subtitle_langs = ["ko-orig", "ko", "en"]

        return cls(
            report_timezone=str(
                data.get("report_timezone", "America/New_York")
            ).strip()
            or "America/New_York",
            lookback_hours=int(data.get("lookback_hours", 18) or 18),
            fetch_article_text=_to_bool(runtime.get("fetch_article_text"), default=True),
            article_fetch_workers=max(1, int(runtime.get("article_fetch_workers", 4) or 4)),
            article_fetch_timeout_seconds=max(
                5, int(runtime.get("article_fetch_timeout_seconds", 20) or 20)
            ),
            article_fetch_retries=max(
                0, int(runtime.get("article_fetch_retries", 1) or 1)
            ),
            subtitle_dir=str(runtime.get("subtitle_dir", "subtitles")).strip()
            or "subtitles",
            yt_dlp_path=str(
                runtime.get("yt_dlp_path", os.getenv("YT_DLP_PATH", "yt-dlp"))
            ).strip()
            or "yt-dlp",
            subtitle_command_timeout_seconds=max(
                5,
                int(runtime.get("subtitle_command_timeout_seconds", 45) or 45),
            ),
            raw_collection_copy_dir=str(
                runtime.get("raw_collection_copy_dir", "")
            ).strip(),
            default_subtitle_langs=default_subtitle_langs,
            filters=filters,
            sources=sources,
            weather_context=weather_context,
            holiday_context=holiday_context,
            history=history,
            weekly_summary=weekly_summary,
            youtube_api_key=os.getenv("YOUTUBE_API_KEY") or None,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            config_file=config_path,
        )

    def rss_sources(self) -> List[SourceConfig]:
        return [
            source
            for source in self.sources
            if source.enabled and source.type == "rss" and source.url
        ]

    def youtube_sources(self) -> List[SourceConfig]:
        return [
            source
            for source in self.sources
            if source.enabled and source.type == "youtube_channel" and source.channel_id
        ]
