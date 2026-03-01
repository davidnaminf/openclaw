---
name: youtube-obsidian-briefing
description: Process a pasted YouTube link into an Obsidian note for the user’s YouTube inbox workflow. Use when the user shares a YouTube URL (especially via Telegram/chat), wants auto categorization (recipe/investing/current-affairs/general), intent-aware summarization, AI evaluation, and save to their Obsidian folder with the existing frontmatter schema.
---

# YouTube -> Obsidian Briefing

## Target vault/path
- Vault path: `/Users/assa_david/Documents/MyAswomeVault`
- Folder path: `/Users/assa_david/Documents/MyAswomeVault/02.YouTubeInbox`

## Output file naming
- Pattern: `YT_<generated_date_YYYYMMDD>_<channel>_<durationSec>_<upload_YYYYMMDD>_<videoId>.md`
- Use safe filename characters only: letters, digits, `_`, `-`.

## Subtitle extraction (fixed profile)
- Run transcript extraction via `scripts/fetch_yt_subs.sh`.
- Default (most stable in this workspace):
  - `bash scripts/fetch_yt_subs.sh "<youtube_url>" "ko" "/tmp"`
- If Korean is missing, retry with:
  - `bash scripts/fetch_yt_subs.sh "<youtube_url>" "ko.*,en.*" "/tmp"`
- Keep single-language first (`ko`) when possible. Multi-language/translation tracks often trigger HTTP 429.
- Set `transcript_source` accurately:
  - auto caption: `subtitle:auto:<lang>`
  - manual subtitle: `subtitle:manual:<lang>`
  - unavailable: `none`

## Required frontmatter schema
Always include these fields exactly (string values unless boolean):

- `title`
- `url`
- `channel`
- `video_id`
- `upload_date` (`YYYY-MM-DD`)
- `transcript_source` (`subtitle:manual:<lang>` / `subtitle:auto:<lang>` / `none`)
- `summary_provider`
- `summary_model`
- `summary_usage_source`
- `summary_input_tokens`
- `summary_output_tokens`
- `summary_cached_input_tokens`
- `summary_reasoning_tokens`
- `summary_total_tokens`
- `summary_pricing_input_usd_per_1m`
- `summary_pricing_cached_input_usd_per_1m`
- `summary_pricing_output_usd_per_1m`
- `summary_estimated_cost_usd`
- `generated_at` (`YYYY-MM-DDTHH:mm:ss` local)
- `read` (boolean, default `false`)
- `important` (boolean, default `false`)

If token/pricing numbers are unavailable, leave as empty string (`""`) instead of inventing values.

## Processing order (must follow)
1. Parse and normalize YouTube URL.
2. Extract subtitles/transcript with `scripts/fetch_yt_subs.sh` (default `ko`, then fallback `ko.*,en.*`).
3. Collect context before summarizing:
   - title
   - description (“more” section)
   - top/pinned comments (if available)
   - channel name
4. Determine category first:
   - `recipe`
   - `investing`
   - `current-affairs`
   - `general-knowledge`
   - `other`
5. Infer author intent in one sentence: “This video is trying to …”.
6. Build category-aware summary.
7. Add AI evaluation + stronger insights.
8. Write note into target folder with required frontmatter.

## Category-specific emphasis

### recipe
Prioritize:
- ingredient list (quantities/ratios if present)
- prep checklist
- cooking steps in order
- failure points and fixes
- substitutions and storage tips

### investing
Prioritize (strict template):
1. **핵심 주장 3줄**: 저자의 결론을 손실 없이 압축
2. **근거 데이터 표**: 영상에서 언급한 숫자/기간/가이던스/실적지표를 표로 정리
3. **가정 vs 사실 분리**:
   - 사실(확인된 수치/공시 기반)
   - 주장(해석/전망)
4. **반대 시나리오/무효화 조건**:
   - 어떤 조건이 나오면 주장이 깨지는지 명시
5. **실행 체크리스트**:
   - 진입 조건
   - 비중 규칙
   - 축소/이탈 규칙
6. **세후 수익률 관점**:
   - 세금 포함한 기대값/의사결정 관점으로 한 줄 평가
7. **점수화**:
   - 데이터 근거 밀도(0~5)
   - 과장도(0~5)
   - 실행가능성(0~5)

### current-affairs
Prioritize:
- facts vs opinions separation
- timeline and stakeholders
- implications (short/medium term)
- what is confirmed vs unclear

### general-knowledge
Prioritize:
- key concepts
- mechanism/how it works
- common misconceptions
- simple examples

## AI evaluation section (always include)
Include these subsections:
- `### 이 내용에 대한 평가/판단`
- `### 함께 보면 좋은 관련 주제`
- `### 여기서 뽑아야 할 인사이트`
- `### 실전 적용 포인트`

## Quality rules
- Do not just paraphrase; extract signal.
- Distinguish facts, claims, and interpretation.
- If transcript is unavailable, state limitations clearly and use description/comments as fallback.
- Avoid hallucinating numbers, timelines, or quotes.

## Save behavior
- Create new note in `02.YouTubeInbox`.
- Keep Korean headings/body style consistent with existing notes.
- Preserve frontmatter field names exactly.
