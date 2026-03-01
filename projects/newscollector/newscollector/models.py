from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List


@dataclass
class NewsItem:
    source: str
    title: str
    url: str
    published_at: datetime
    summary: str = ""
    article_text: str = ""
    article_text_file: str | None = None
    why_it_matters: str = ""
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at.isoformat(),
            "excerpt": self.summary,
            "summary": self.summary,
            "article_text": self.article_text,
            "article_text_file": self.article_text_file,
            "why_it_matters": self.why_it_matters,
            "impact_score": self.score,
        }


@dataclass
class VideoItem:
    channel_id: str
    channel_title: str
    video_id: str
    title: str
    url: str
    published_at: datetime
    description: str = ""
    transcript_text: str = ""
    transcript_file: str | None = None
    subtitle_lang: str | None = None
    subtitle_type: str | None = None
    summary: str = ""
    view_count: int | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel_title,
            "channel_id": self.channel_id,
            "video_id": self.video_id,
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at.isoformat(),
            "view_count": self.view_count,
            "description": self.description,
            "transcript_text": self.transcript_text,
            "transcript_file": self.transcript_file,
            "subtitle_lang": self.subtitle_lang,
            "subtitle_type": self.subtitle_type,
            "summary": self.summary,
        }


@dataclass
class DailyReport:
    report_date: str
    generated_at: str
    headlines: List[NewsItem] = field(default_factory=list)
    youtube: List[VideoItem] = field(default_factory=list)
    macro: List[NewsItem] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.report_date,
            "generated_at": self.generated_at,
            "headlines": [item.to_dict() for item in self.headlines],
            "youtube": [item.to_dict() for item in self.youtube],
            "macro": [item.to_dict() for item in self.macro],
        }
