---
name: stt-from-mom
description: Process meeting videos/audio from Google Drive _inbox into compressed MP4 + MP3 + TXT/SRT/JSON + Korean/English meeting minutes under Processed/<file_stem>. Use when the user asks to run, debug, or schedule the STT_from_MoM workflow on macOS.
metadata:
  {
    "openclaw":
      {
        "emoji": "🎧",
        "requires":
          { "bins": ["python3", "openclaw"], "env": ["ASSEMBLYAI_API_KEY"] },
      },
  }
---

# STT from MoM (macOS)

## Purpose
- Input folder: `Infineon/_inbox`
- Output folder: `Infineon/Processed/<file_stem>/`
- Input archive folders:
  - success: `Infineon/_inbox/_processed`
  - failure: `Infineon/_inbox/_failed`

## What this skill runs
- `scripts/process_inbox.py`:
  - scans `_inbox` for `mp4/mp3/wav/m4a`
  - for mp4: creates low-size mp4 copy + mp3
  - runs transcription + subtitle + meeting-minutes generation
  - moves source input to `_processed` or `_failed`
- `scripts/transcribe_audio.py` and `scripts/extract_audio.py` are migrated from legacy Windows workflow and used as the core engine.

## One-shot run
```bash
bash {baseDir}/scripts/run_inbox.sh --config {baseDir}/config/default.json
```

Dry-run:
```bash
bash {baseDir}/scripts/run_inbox.sh --config {baseDir}/config/default.json --dry-run
```

## Required keys
- `ASSEMBLYAI_API_KEY`
- `OPENAI_API_KEY` (only when `llm_provider` is `openai-api`)

Example:
```bash
export ASSEMBLYAI_API_KEY="..."
export OPENAI_API_KEY="..."
```

Runtime key resolution order:
1. Current process environment
2. macOS `launchctl getenv` values
3. OpenClaw auth profile `openai:default` (api_key), if present

Default LLM path:
- `llm_provider: openclaw-agent`
- `llm_model: openai-codex/gpt-5.2`
- Uses OpenClaw session model/auth (Codex OAuth)

## Config notes
- File: `{baseDir}/config/default.json`
- Leave `infineon_root` empty to auto-detect from macOS Google Drive mount (`~/Library/CloudStorage/GoogleDrive-*/.../Infineon`).
- Set explicit paths if auto-detect is unreliable.

Main knobs:
- `processed_output_dir`: defaults to `<Infineon>/Processed`
- `video_max_width`, `video_fps`, `video_crf`: mp4 size/quality
- `audio_bitrate`: mp3 bitrate
- `summary_mode`, `llm_provider`, `llm_model`, `mom_detail_ko`, `mom_detail_en`
- Obsidian copy paths (optional): `meeting_english_vault_dir`, `meeting_english_expressions_dir`, `mom_vault_dir`
- `disable_extra_copies`: `true`이면 Obsidian/보조 폴더 복사 비활성화

## Processing order (fixed)
1. Pick stable files from `_inbox`.
2. Create result directory `Processed/<stem>`.
3. For mp4 input:
   - compress video to low-size mp4
   - extract mp3
4. Generate transcript outputs via `transcribe_audio.py`:
   - `*.txt`, `*.srt`, `*.json`, `*_MoM_KO.md`, `*_MoM_EN.md`
5. Move original input to `_inbox/_processed`.
6. On error, move original input to `_inbox/_failed`.
