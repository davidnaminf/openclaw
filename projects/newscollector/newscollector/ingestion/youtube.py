from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

import httpx

from newscollector.models import VideoItem

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


def _parse_yt_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(tz=timezone.utc)
    if value.endswith("Z"):
        value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value)


def _fetch_video_details(
    client: httpx.Client, api_key: str, video_ids: List[str]
) -> Dict[str, Dict[str, object]]:
    if not video_ids:
        return {}
    params = {
        "part": "snippet,statistics",
        "id": ",".join(video_ids),
        "key": api_key,
        "maxResults": 50,
    }
    response = client.get(f"{YOUTUBE_API_BASE}/videos", params=params)
    if response.status_code != 200:
        return {}
    payload = response.json()
    out: Dict[str, Dict[str, object]] = {}
    for item in payload.get("items", []):
        video_id = item.get("id")
        if not video_id:
            continue
        out[str(video_id)] = item
    return out


def fetch_recent_videos(
    api_key: str,
    channel_ids: List[str],
    published_after_utc: datetime,
    max_per_channel: int = 5,
) -> List[VideoItem]:
    if not api_key or not channel_ids:
        return []

    videos: List[VideoItem] = []
    published_after = published_after_utc.astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )

    with httpx.Client(timeout=20) as client:
        for requested_channel_id in channel_ids:
            params = {
                "part": "snippet",
                "channelId": requested_channel_id,
                "publishedAfter": published_after,
                "type": "video",
                "order": "date",
                "maxResults": max_per_channel,
                "key": api_key,
            }
            response = client.get(f"{YOUTUBE_API_BASE}/search", params=params)
            if response.status_code != 200:
                continue

            payload = response.json()
            video_ids: List[str] = []
            for item in payload.get("items", []):
                item_id = item.get("id", {})
                video_id = item_id.get("videoId")
                if video_id:
                    video_ids.append(str(video_id))

            detail_map = _fetch_video_details(client, api_key, video_ids)
            for video_id in video_ids:
                details = detail_map.get(video_id, {})
                snippet = details.get("snippet", {})
                stats = details.get("statistics", {})
                title = str(snippet.get("title", "")).strip()
                channel_title = str(snippet.get("channelTitle", "")).strip()
                item_channel_id = str(snippet.get("channelId", "")).strip()
                description = str(snippet.get("description", "")).strip()
                published_at = _parse_yt_datetime(snippet.get("publishedAt"))
                if not title:
                    continue
                view_count = stats.get("viewCount")
                videos.append(
                    VideoItem(
                        channel_id=item_channel_id or requested_channel_id,
                        channel_title=channel_title or requested_channel_id,
                        video_id=video_id,
                        title=title,
                        url=f"https://www.youtube.com/watch?v={video_id}",
                        published_at=published_at,
                        description=description,
                        view_count=int(view_count) if str(view_count).isdigit() else None,
                    )
                )
    return videos
