from __future__ import annotations

import calendar
from datetime import datetime, timezone
from typing import Dict, List

import feedparser

from newscollector.models import NewsItem


def _parse_published(entry: object) -> datetime | None:
    published_parsed = getattr(entry, "published_parsed", None)
    if published_parsed:
        ts = calendar.timegm(published_parsed)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    updated_parsed = getattr(entry, "updated_parsed", None)
    if updated_parsed:
        ts = calendar.timegm(updated_parsed)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def _summary(entry: object) -> str:
    if getattr(entry, "summary", None):
        return str(getattr(entry, "summary")).strip()
    if getattr(entry, "description", None):
        return str(getattr(entry, "description")).strip()
    return ""


def fetch_rss_items(feed_map: Dict[str, str], cutoff_utc: datetime) -> List[NewsItem]:
    items: List[NewsItem] = []
    for source_name, feed_url in feed_map.items():
        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries:
            published_at = _parse_published(entry)
            if not published_at:
                continue
            if published_at < cutoff_utc:
                continue
            title = str(getattr(entry, "title", "")).strip()
            link = str(getattr(entry, "link", "")).strip()
            if not title or not link:
                continue
            items.append(
                NewsItem(
                    source=source_name,
                    title=title,
                    url=link,
                    published_at=published_at,
                    summary=_summary(entry),
                )
            )
    return items
