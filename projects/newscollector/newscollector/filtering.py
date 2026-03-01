from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List

from newscollector.models import NewsItem


def normalize_title(value: str) -> str:
    lowered = value.lower().strip()
    lowered = re.sub(r"\s+", " ", lowered)
    lowered = re.sub(r"[^a-z0-9 ]", "", lowered)
    return lowered


def _matches_token(text: str, token: str) -> bool:
    token = token.lower().strip()
    if not token:
        return False
    if " " in token:
        return token in text
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def dedupe_news(items: Iterable[NewsItem]) -> List[NewsItem]:
    seen = set()
    deduped: List[NewsItem] = []
    for item in sorted(items, key=lambda x: x.published_at, reverse=True):
        key = normalize_title(item.title)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def filter_by_topics(items: Iterable[NewsItem], topics: List[str]) -> List[NewsItem]:
    if not topics:
        return list(items)

    checks = [topic.lower().strip() for topic in topics if topic.strip()]
    filtered: List[NewsItem] = []
    for item in items:
        text = f"{item.title} {item.summary}".lower()
        if any(_matches_token(text, token) for token in checks):
            filtered.append(item)
    return filtered


def score_news(
    items: Iterable[NewsItem],
    topics: List[str],
    source_weights: Dict[str, float],
    now_utc: datetime | None = None,
) -> List[NewsItem]:
    now_utc = now_utc or datetime.now(tz=timezone.utc)
    checks = [topic.lower().strip() for topic in topics if topic.strip()]
    scored: List[NewsItem] = []

    for item in items:
        text = f"{item.title} {item.summary}".lower()
        topic_hits = sum(1 for token in checks if _matches_token(text, token))
        source_weight = source_weights.get(item.source, 1.0)

        age_hours = max((now_utc - item.published_at).total_seconds() / 3600.0, 0.0)
        recency_score = max(0.0, 24.0 - age_hours) / 24.0

        item.score = round((source_weight * 2.0) + (topic_hits * 1.25) + recency_score, 2)
        scored.append(item)

    scored.sort(key=lambda x: x.score, reverse=True)
    return scored
