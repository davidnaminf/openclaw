# NewsCollector - Raw Source Collector

Collects original items from configured sources:

- RSS feeds
- YouTube channels (optional)
- YouTube subtitles via `yt-dlp` for selected channels (optional)
- Context sources (optional): Seoul weather forecast + public holidays
- Persistent history DB (SQLite)
- Weekly-window template report generation (optional OpenAI)

Current scope:
- raw collection (RSS/YouTube/context)
- history accumulation
- recent 7-day template report generation

## Quick Start

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
copy .env.example .env
python -m newscollector.main
```

Outputs are written to `output/`:

- `output/raw_collection_YYYYMMDD.json`
- `output/raw_collection_YYYYMMDD.md`
- `output/raw_text_YYYYMMDD/rss/*.txt` (full article text)
- `output/raw_text_YYYYMMDD/youtube/*.txt` (full transcript text)
- `output/history/newscollector.sqlite3` (persistent item store)
- `output/weekly_reports/daily_summary_YYYYMMDD.md` (template report)

## One-File Control (Sources + Filters)

Edit only `config/pipeline.json`.

### Sources

Each source has `type`:

- `rss`: needs `url`
- `youtube_channel`: needs `channel_id`

Example:

```json
{
  "id": "cnbc_topnews",
  "type": "rss",
  "name": "CNBC",
  "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
  "enabled": true
}
```

```json
{
  "id": "juns_economy",
  "type": "youtube_channel",
  "name": "JunsEconomyLab",
  "channel_id": "UCznImSIaxZR7fdLCICLdgaQ",
  "enabled": true,
  "extract_subtitles": true,
  "subtitle_langs": ["ko-orig", "ko", "en"]
}
```

### Filters

All filter settings are under `filters` in `config/pipeline.json`:

- `topic_keywords`
- `dedupe_titles`
- `include_sources`, `exclude_sources` (use source `id`)
- `source_weights`, `min_score`
- `max_news_items`, `max_videos`
- `apply_topic_filter_to_youtube`

### Runtime

Runtime options are under `runtime` in `config/pipeline.json`:

- `fetch_article_text`
- `article_fetch_workers`
- `article_fetch_timeout_seconds`
- `article_fetch_retries`
- `subtitle_dir`
- `yt_dlp_path`
- `subtitle_command_timeout_seconds`
- `raw_collection_copy_dir` (optional mirror dir for `raw_collection_YYYYMMDD.md`)
- `default_subtitle_langs`

### History / Weekly Summary

- `history.bootstrap_lookback_days`: first run when DB is empty (default 7 days)
- `history.weekly_window_days`: rolling window used for summary input (default 7 days)
- `weekly_summary.template_path`: markdown template path
- `weekly_summary.output_dir`: rendered report output directory
- `weekly_summary.model`: OpenAI model name
- rendered summary frontmatter includes:
  - `Summary model`
  - `LLM prompt tokens`, `LLM completion tokens`, `LLM total tokens`
  - `LLM estimated cost (USD)` (model pricing-based estimate)

If `OPENAI_API_KEY` is missing, weekly summary runs in heuristic mode.

### Context Sources

`context_sources` in `config/pipeline.json` controls non-news context inputs:

- `weather` (Open-Meteo): Seoul forecast snapshot
- `holidays` (Nager.Date): public holidays by country window

Example:

```json
"context_sources": {
  "weather": {
    "enabled": true,
    "provider": "open_meteo",
    "location_name": "Seoul",
    "latitude": 37.5665,
    "longitude": 126.9780,
    "timezone": "Asia/Seoul",
    "forecast_days": 1
  },
  "holidays": {
    "enabled": true,
    "provider": "nager_date",
    "country_codes": ["KR", "US", "DE", "AT", "CN"],
    "window_days": 7
  }
}
```

## Environment Variables

In `.env`:

- `PIPELINE_CONFIG_FILE` (default: `config/pipeline.json`)
- `YOUTUBE_API_KEY` (only if YouTube sources are enabled)
- `OPENAI_API_KEY` (optional, for AI-written weekly sections)
- `YT_DLP_PATH` (optional override)

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```
