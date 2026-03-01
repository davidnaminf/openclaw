import subprocess
from pathlib import Path

from newscollector.ingestion import youtube_subtitles
from newscollector.ingestion.youtube_subtitles import (
    choose_subtitle_language,
    clean_subtitle_text,
    fetch_video_transcript,
    parse_list_subs_output,
)


def test_parse_list_subs_output():
    sample = """
Available subtitles for abc:
Language Name
ko-orig Korean
en English

Available automatic captions for abc:
Language Name
ko Korean (auto generated)
"""
    manual, auto = parse_list_subs_output(sample)
    assert manual == ["ko-orig", "en"]
    assert auto == ["ko"]


def test_choose_subtitle_language_prefers_manual():
    sub_type, lang = choose_subtitle_language(
        preferred_langs=["ko-orig", "ko", "en"],
        manual_langs=["en"],
        auto_langs=["ko"],
    )
    assert sub_type == "manual"
    assert lang == "en"


def test_clean_subtitle_text(tmp_path: Path):
    subtitle = tmp_path / "x.vtt"
    subtitle.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n<00:00:00.000>Hello\n"
        "00:00:02.000 --> 00:00:04.000\nHello\n"
        "00:00:04.000 --> 00:00:06.000\nWorld\n",
        encoding="utf-8",
    )
    cleaned = clean_subtitle_text(subtitle)
    assert "Hello" in cleaned
    assert "World" in cleaned


def test_fetch_video_transcript_times_out_on_list_subs(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        youtube_subtitles,
        "ensure_yt_dlp_available",
        lambda _: True,
    )

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=7)

    monkeypatch.setattr(youtube_subtitles, "_run_command", _raise_timeout)

    result = fetch_video_transcript(
        yt_dlp="yt-dlp",
        video_url="https://www.youtube.com/watch?v=abc",
        output_dir=tmp_path,
        preferred_langs=["ko"],
        command_timeout_seconds=7,
    )

    assert result.has_subtitles is False
    assert result.error == "--list-subs timed out after 7s"


def test_fetch_video_transcript_times_out_on_download(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        youtube_subtitles,
        "ensure_yt_dlp_available",
        lambda _: True,
    )
    list_output = (
        "Available subtitles for abc:\n"
        "Language Name\n"
        "ko Korean\n"
    )
    calls = {"count": 0}

    def _run_with_download_timeout(args, timeout_seconds):
        calls["count"] += 1
        if calls["count"] == 1:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=list_output,
                stderr="",
            )
        raise subprocess.TimeoutExpired(cmd=args, timeout=timeout_seconds)

    monkeypatch.setattr(
        youtube_subtitles,
        "_run_command",
        _run_with_download_timeout,
    )

    result = fetch_video_transcript(
        yt_dlp="yt-dlp",
        video_url="https://www.youtube.com/watch?v=abc",
        output_dir=tmp_path,
        preferred_langs=["ko"],
        command_timeout_seconds=11,
    )

    assert result.has_subtitles is False
    assert result.error == "subtitle download timed out after 11s"
