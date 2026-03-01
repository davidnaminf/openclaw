from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from newscollector.models import DailyReport


def _compact(text: str, limit: int = 260) -> str:
    clean = re.sub(r"<[^>]+>", " ", text or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _slug(text: str, max_len: int = 80) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip())
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        value = "item"
    return value[:max_len]


def _save_raw_text_artifacts(report: DailyReport, output_dir: Path, stamp: str) -> None:
    base_dir = output_dir / f"raw_text_{stamp}"
    rss_dir = base_dir / "rss"
    youtube_dir = base_dir / "youtube"
    for target_dir in (rss_dir, youtube_dir):
        target_dir.mkdir(parents=True, exist_ok=True)
        # Re-running on same date should not accumulate stale files.
        for existing in target_dir.glob("*.txt"):
            existing.unlink()

    for idx, item in enumerate(report.headlines, start=1):
        if not item.article_text:
            continue
        filename = f"{idx:03d}_{_slug(item.source)}_{_slug(item.title)}.txt"
        path = rss_dir / filename
        path.write_text(item.article_text.rstrip() + "\n", encoding="utf-8")
        item.article_text_file = str(path.relative_to(output_dir)).replace("\\", "/")

    for video in report.youtube:
        if not video.transcript_text:
            continue
        filename = f"{_slug(video.channel_title)}_{video.video_id}.txt"
        path = youtube_dir / filename
        path.write_text(video.transcript_text.rstrip() + "\n", encoding="utf-8")
        video.transcript_file = str(path.relative_to(output_dir)).replace("\\", "/")


def to_markdown(report: DailyReport) -> str:
    lines = [
        f"# Raw Source Collection ({report.report_date})",
        "",
        f"Generated at: {report.generated_at}",
        "",
        "## RSS Items",
        "",
    ]
    if not report.headlines:
        lines.append("- No RSS items after current filter settings.")
    else:
        for idx, item in enumerate(report.headlines, start=1):
            lines.append(f"{idx}. [{item.title}]({item.url}) - {item.source}")
            lines.append(f"   - Published: {item.published_at.isoformat()}")
            if item.score > 0:
                lines.append(f"   - Score: {item.score:.2f}")
            if item.article_text:
                lines.append(f"   - Article Text Chars: {len(item.article_text)}")
            if item.article_text_file:
                lines.append(f"   - Article Text File: {item.article_text_file}")
            elif item.summary:
                lines.append(f"   - Excerpt: {_compact(item.summary)}")

    lines.extend(["", "## YouTube Items", ""])
    if not report.youtube:
        lines.append("- No YouTube items after current filter settings.")
    else:
        for idx, video in enumerate(report.youtube, start=1):
            lines.append(f"{idx}. [{video.title}]({video.url}) - {video.channel_title}")
            lines.append(f"   - Published: {video.published_at.isoformat()}")
            if video.subtitle_lang:
                lines.append(
                    f"   - Subtitle: {video.subtitle_type or 'unknown'} / {video.subtitle_lang}"
                )
            if video.transcript_text:
                lines.append(f"   - Transcript Chars: {len(video.transcript_text)}")
            if video.transcript_file:
                lines.append(f"   - Transcript File: {video.transcript_file}")
            elif video.description:
                lines.append(f"   - Description: {_compact(video.description)}")

    lines.extend(["", "## Macro Context", ""])
    if not report.macro:
        lines.append("- No macro context items.")
    else:
        for idx, item in enumerate(report.macro, start=1):
            lines.append(f"{idx}. {item.title} - {item.source}")
            if item.url:
                lines.append(f"   - Source URL: {item.url}")
            lines.append(f"   - Published: {item.published_at.isoformat()}")
            if item.summary:
                lines.append(f"   - Details: {_compact(item.summary, limit=500)}")
            if item.why_it_matters:
                lines.append(f"   - Why it matters: {_compact(item.why_it_matters)}")

    lines.append("")
    return "\n".join(lines)


def save_report(
    report: DailyReport,
    output_dir: Path,
    mirror_md_dir: Path | None = None,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.report_date.replace("-", "")
    json_path = output_dir / f"raw_collection_{stamp}.json"
    md_path = output_dir / f"raw_collection_{stamp}.md"

    _save_raw_text_artifacts(report, output_dir, stamp)

    with json_path.open("w", encoding="utf-8") as fp:
        json.dump(report.to_dict(), fp, ensure_ascii=False, indent=2)
    with md_path.open("w", encoding="utf-8") as fp:
        fp.write(to_markdown(report))
    if mirror_md_dir is not None:
        mirror_md_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(md_path, mirror_md_dir / md_path.name)

    return json_path, md_path
