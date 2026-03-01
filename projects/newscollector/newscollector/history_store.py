from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

from newscollector.models import DailyReport, NewsItem, VideoItem


def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value)


@dataclass
class HistoryWindow:
    start_utc: datetime
    end_utc: datetime
    headlines: List[NewsItem]
    youtube: List[VideoItem]
    macro: List[NewsItem]


class HistoryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS rss_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    why_it_matters TEXT NOT NULL DEFAULT '',
                    article_text TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_rss_published_at
                ON rss_items (published_at DESC);

                CREATE TABLE IF NOT EXISTS youtube_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL UNIQUE,
                    channel_id TEXT NOT NULL,
                    channel_title TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    transcript_text TEXT NOT NULL DEFAULT '',
                    subtitle_lang TEXT,
                    subtitle_type TEXT,
                    view_count INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_youtube_published_at
                ON youtube_items (published_at DESC);

                CREATE TABLE IF NOT EXISTS macro_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    why_it_matters TEXT NOT NULL DEFAULT '',
                    UNIQUE (source, title, published_at)
                );

                CREATE INDEX IF NOT EXISTS idx_macro_published_at
                ON macro_items (published_at DESC);
                """
            )

    def has_any_items(self) -> bool:
        with self._connect() as conn:
            rss_count = conn.execute("SELECT COUNT(1) FROM rss_items").fetchone()[0]
            yt_count = conn.execute("SELECT COUNT(1) FROM youtube_items").fetchone()[0]
            macro_count = conn.execute("SELECT COUNT(1) FROM macro_items").fetchone()[0]
        return bool(rss_count or yt_count or macro_count)

    def upsert_report(self, report: DailyReport) -> None:
        collected_at = report.generated_at
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO rss_items (
                    url, source, title, published_at, collected_at,
                    score, summary, why_it_matters, article_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    source=excluded.source,
                    title=excluded.title,
                    published_at=excluded.published_at,
                    collected_at=excluded.collected_at,
                    score=excluded.score,
                    summary=excluded.summary,
                    why_it_matters=excluded.why_it_matters,
                    article_text=CASE
                        WHEN excluded.article_text <> '' THEN excluded.article_text
                        ELSE rss_items.article_text
                    END
                """,
                [
                    (
                        item.url,
                        item.source,
                        item.title,
                        _to_utc_iso(item.published_at),
                        collected_at,
                        float(item.score),
                        item.summary or "",
                        item.why_it_matters or "",
                        item.article_text or "",
                    )
                    for item in report.headlines
                ],
            )
            conn.executemany(
                """
                INSERT INTO youtube_items (
                    video_id, channel_id, channel_title, title, url,
                    published_at, collected_at, description, transcript_text,
                    subtitle_lang, subtitle_type, view_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    channel_id=excluded.channel_id,
                    channel_title=excluded.channel_title,
                    title=excluded.title,
                    url=excluded.url,
                    published_at=excluded.published_at,
                    collected_at=excluded.collected_at,
                    description=excluded.description,
                    transcript_text=CASE
                        WHEN excluded.transcript_text <> '' THEN excluded.transcript_text
                        ELSE youtube_items.transcript_text
                    END,
                    subtitle_lang=excluded.subtitle_lang,
                    subtitle_type=excluded.subtitle_type,
                    view_count=excluded.view_count
                """,
                [
                    (
                        item.video_id,
                        item.channel_id,
                        item.channel_title,
                        item.title,
                        item.url,
                        _to_utc_iso(item.published_at),
                        collected_at,
                        item.description or "",
                        item.transcript_text or "",
                        item.subtitle_lang,
                        item.subtitle_type,
                        item.view_count,
                    )
                    for item in report.youtube
                ],
            )
            conn.executemany(
                """
                INSERT INTO macro_items (
                    source, title, url, published_at, collected_at, summary, why_it_matters
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, title, published_at) DO UPDATE SET
                    url=excluded.url,
                    collected_at=excluded.collected_at,
                    summary=excluded.summary,
                    why_it_matters=excluded.why_it_matters
                """,
                [
                    (
                        item.source,
                        item.title,
                        item.url or "",
                        _to_utc_iso(item.published_at),
                        collected_at,
                        item.summary or "",
                        item.why_it_matters or "",
                    )
                    for item in report.macro
                ],
            )

    def load_window(self, end_utc: datetime, days: int) -> HistoryWindow:
        end = end_utc.astimezone(timezone.utc)
        start = end - timedelta(days=max(1, int(days)))
        start_iso = start.isoformat()
        end_iso = end.isoformat()

        with self._connect() as conn:
            rss_rows = conn.execute(
                """
                SELECT source, title, url, published_at, summary, why_it_matters, score, article_text
                FROM rss_items
                WHERE published_at >= ? AND published_at <= ?
                ORDER BY published_at DESC
                """,
                (start_iso, end_iso),
            ).fetchall()
            yt_rows = conn.execute(
                """
                SELECT channel_id, channel_title, video_id, title, url, published_at,
                       description, transcript_text, subtitle_lang, subtitle_type, view_count
                FROM youtube_items
                WHERE published_at >= ? AND published_at <= ?
                ORDER BY published_at DESC
                """,
                (start_iso, end_iso),
            ).fetchall()
            macro_rows = conn.execute(
                """
                SELECT source, title, url, published_at, summary, why_it_matters
                FROM macro_items
                WHERE published_at >= ? AND published_at <= ?
                ORDER BY published_at DESC
                """,
                (start_iso, end_iso),
            ).fetchall()

        headlines = [
            NewsItem(
                source=row["source"],
                title=row["title"],
                url=row["url"],
                published_at=_parse_iso(row["published_at"]),
                summary=row["summary"] or "",
                why_it_matters=row["why_it_matters"] or "",
                score=float(row["score"] or 0.0),
                article_text=row["article_text"] or "",
            )
            for row in rss_rows
        ]
        youtube = [
            VideoItem(
                channel_id=row["channel_id"],
                channel_title=row["channel_title"],
                video_id=row["video_id"],
                title=row["title"],
                url=row["url"],
                published_at=_parse_iso(row["published_at"]),
                description=row["description"] or "",
                transcript_text=row["transcript_text"] or "",
                subtitle_lang=row["subtitle_lang"],
                subtitle_type=row["subtitle_type"],
                view_count=row["view_count"],
            )
            for row in yt_rows
        ]
        macro = [
            NewsItem(
                source=row["source"],
                title=row["title"],
                url=row["url"],
                published_at=_parse_iso(row["published_at"]),
                summary=row["summary"] or "",
                why_it_matters=row["why_it_matters"] or "",
            )
            for row in macro_rows
        ]

        return HistoryWindow(
            start_utc=start,
            end_utc=end,
            headlines=headlines,
            youtube=youtube,
            macro=macro,
        )
