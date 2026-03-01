from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

SUB_EXTENSIONS = {
    ".vtt",
    ".srt",
    ".ass",
    ".ssa",
    ".ttml",
    ".srv1",
    ".srv2",
    ".srv3",
    ".json3",
}


@dataclass
class SubtitleResult:
    has_subtitles: bool
    subtitle_type: str | None = None
    subtitle_lang: str | None = None
    subtitle_file: Path | None = None
    transcript_text: str = ""
    error: str | None = None


def ensure_yt_dlp_available(binary: str) -> bool:
    if shutil.which(binary):
        return True
    return Path(binary).exists()


def _run_command(
    args: Sequence[str],
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        timeout=max(1, int(timeout_seconds)),
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def parse_list_subs_output(output: str) -> tuple[list[str], list[str]]:
    manual_langs: list[str] = []
    auto_langs: list[str] = []
    section: str | None = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        lower = line.lower()

        if "available subtitles" in lower:
            section = "manual"
            continue
        if "available automatic captions" in lower:
            section = "auto"
            continue
        if not section:
            continue
        if not line:
            continue
        if line.startswith("["):
            section = None
            continue
        if lower.startswith("language"):
            continue

        lang = line.split()[0]
        if not re.fullmatch(r"[A-Za-z0-9._-]+", lang):
            continue
        if section == "manual" and lang not in manual_langs:
            manual_langs.append(lang)
        elif section == "auto" and lang not in auto_langs:
            auto_langs.append(lang)

    return manual_langs, auto_langs


def choose_subtitle_language(
    preferred_langs: Sequence[str],
    manual_langs: Sequence[str],
    auto_langs: Sequence[str],
) -> tuple[str | None, str | None]:
    for lang in preferred_langs:
        if lang in manual_langs:
            return "manual", lang
    for lang in preferred_langs:
        if lang in auto_langs:
            return "auto", lang
    if manual_langs:
        return "manual", manual_langs[0]
    if auto_langs:
        return "auto", auto_langs[0]
    return None, None


def _subtitle_files_in(directory: Path) -> set[Path]:
    if not directory.exists():
        return set()
    files: set[Path] = set()
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUB_EXTENSIONS:
            files.add(path.resolve())
    return files


def _extract_subtitle_paths_from_log(text: str) -> list[Path]:
    patterns = [
        r"(?:Writing video subtitles to|Writing video automatic captions to):\s*(.+)$",
        r"Destination:\s*(.+)$",
    ]
    found: list[Path] = []
    for line in text.splitlines():
        s = line.strip()
        for pattern in patterns:
            m = re.search(pattern, s)
            if not m:
                continue
            candidate = Path(m.group(1).strip().strip('"'))
            if candidate.suffix.lower() in SUB_EXTENSIONS:
                found.append(candidate)
    unique: list[Path] = []
    seen: set[str] = set()
    for p in found:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


def clean_subtitle_text(path: Path) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    cleaned: list[str] = []
    prev = ""

    for raw in lines:
        text = raw.strip()
        if not text:
            continue
        if text.startswith("WEBVTT") or text.startswith("Kind:") or text.startswith("Language:"):
            continue
        if re.match(r"^\d\d:\d\d:\d\d\.\d{3}\s+-->", text):
            continue
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&nbsp;", " ").strip()
        if not text or text == prev:
            continue
        cleaned.append(text)
        prev = text

    return "\n".join(cleaned)


def fetch_video_transcript(
    yt_dlp: str,
    video_url: str,
    output_dir: Path,
    preferred_langs: Sequence[str],
    command_timeout_seconds: int = 45,
) -> SubtitleResult:
    if not ensure_yt_dlp_available(yt_dlp):
        return SubtitleResult(
            has_subtitles=False,
            error=f"yt-dlp not found: {yt_dlp}",
        )

    try:
        timeout_seconds = max(1, int(command_timeout_seconds))
    except (TypeError, ValueError):
        timeout_seconds = 45

    list_cmd = [yt_dlp, "--list-subs", video_url]
    try:
        list_result = _run_command(
            list_cmd,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return SubtitleResult(
            has_subtitles=False,
            error=f"--list-subs timed out after {timeout_seconds}s",
        )

    list_output = (list_result.stdout or "") + (list_result.stderr or "")
    if list_result.returncode != 0:
        return SubtitleResult(
            has_subtitles=False,
            error=f"--list-subs failed: {list_output.strip()[:300]}",
        )

    manual_langs, auto_langs = parse_list_subs_output(list_output)
    sub_type, selected_lang = choose_subtitle_language(
        preferred_langs=preferred_langs,
        manual_langs=manual_langs,
        auto_langs=auto_langs,
    )
    if not sub_type or not selected_lang:
        return SubtitleResult(has_subtitles=False)

    output_dir.mkdir(parents=True, exist_ok=True)
    before = _subtitle_files_in(output_dir)

    cmd: list[str] = [
        yt_dlp,
        "--skip-download",
        "--sub-lang",
        selected_lang,
        "--sub-format",
        "vtt",
        "-P",
        str(output_dir),
        "-o",
        "%(id)s.%(ext)s",
        video_url,
    ]
    if sub_type == "manual":
        cmd.insert(2, "--write-subs")
    else:
        cmd.insert(2, "--write-auto-subs")

    try:
        dl_result = _run_command(
            cmd,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return SubtitleResult(
            has_subtitles=False,
            error=f"subtitle download timed out after {timeout_seconds}s",
        )

    dl_output = (dl_result.stdout or "") + (dl_result.stderr or "")
    if dl_result.returncode != 0:
        return SubtitleResult(
            has_subtitles=False,
            error=f"subtitle download failed: {dl_output.strip()[:300]}",
        )

    after = _subtitle_files_in(output_dir)
    new_files = sorted(after - before, key=lambda p: p.stat().st_mtime)
    if not new_files:
        parsed = _extract_subtitle_paths_from_log(dl_output)
        for path in parsed:
            resolved = path if path.is_absolute() else (Path.cwd() / path)
            if resolved.exists():
                new_files.append(resolved.resolve())

    if not new_files:
        return SubtitleResult(
            has_subtitles=False,
            error="subtitle reported but no file found",
        )

    subtitle_file = new_files[0]
    transcript = clean_subtitle_text(subtitle_file)
    if not transcript.strip():
        return SubtitleResult(
            has_subtitles=False,
            error=f"subtitle empty after cleaning: {subtitle_file}",
        )

    return SubtitleResult(
        has_subtitles=True,
        subtitle_type=sub_type,
        subtitle_lang=selected_lang,
        subtitle_file=subtitle_file,
        transcript_text=transcript,
    )
