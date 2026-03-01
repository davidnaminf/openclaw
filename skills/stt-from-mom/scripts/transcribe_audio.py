from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest
from uuid import uuid4

import assemblyai as aai


SUPPORTED_EXTS = {".mp3", ".wav", ".m4a", ".mp4"}
EXT_PRIORITY = [".mp3", ".wav", ".m4a", ".mp4"]
DEFAULT_OUTPUT_BASE_DIR = Path("G:/\ub0b4 \ub4dc\ub77c\uc774\ube0c/Infineon")
DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_PROVIDER = "openclaw-agent"
DEFAULT_LLM_MODEL = "openai-codex/gpt-5.2"
DEFAULT_MEETING_ENGLISH_LLM_MODEL = "openai-codex/gpt-5.2"
DEFAULT_OPENCLAW_BIN = "openclaw"
DEFAULT_OPENCLAW_AGENT_ID = "main"
DEFAULT_OPENCLAW_SESSION_PREFIX = "stt-from-mom"
DEFAULT_LLM_THINKING = "low"
DEFAULT_MEETING_ENGLISH_VAULT_DIR = Path("C:/Users/namsh/Documents/MyAswomeVault/06. English/04. Meeting")
DEFAULT_MOM_VAULT_DIR = Path("C:/Users/namsh/Documents/MyAswomeVault/10.WorkFlow/01.Meeting/00.MoM")
DEFAULT_MEETING_ENGLISH_EXPRESSIONS_DIR = Path(
    "C:/Users/namsh/Documents/MyAswomeVault/10.WorkFlow/01.Meeting/99.English Expressions"
)
MAX_LLM_INPUT_CHARS = 120_000
MAX_SUBTITLE_TRANSLATION_ITEMS_PER_BATCH = 80
MAX_SUBTITLE_TRANSLATION_CHARS_PER_BATCH = 10_000
MOM_DETAIL_PROFILES: dict[str, dict[str, int | str]] = {
    "short": {
        "overview_sentences": 2,
        "overview_max_chars": 320,
        "key_points": 8,
        "decisions": 6,
        "actions": 8,
        "ko_summary_hint": "2~3문장",
        "en_summary_hint": "2-3 short paragraphs",
        "llm_length_instruction": "Keep it concise.",
    },
    "normal": {
        "overview_sentences": 3,
        "overview_max_chars": 600,
        "key_points": 16,
        "decisions": 12,
        "actions": 16,
        "ko_summary_hint": "4~6문장",
        "en_summary_hint": "4-6 paragraphs",
        "llm_length_instruction": "Provide practical detail with moderate length.",
    },
    "long": {
        "overview_sentences": 5,
        "overview_max_chars": 1200,
        "key_points": 28,
        "decisions": 20,
        "actions": 24,
        "ko_summary_hint": "8~12문장",
        "en_summary_hint": "8-12 paragraphs",
        "llm_length_instruction": (
            "Be very detailed. Capture as many concrete discussion points, decisions, and action items as possible."
        ),
    },
}
ACTION_KEYWORDS = (
    "action",
    "todo",
    "next step",
    "follow up",
    "owner",
    "deadline",
    "need to",
    "해야",
    "하기로",
    "하겠습니다",
    "합시다",
    "확인",
    "정리",
    "공유",
    "검토",
    "전달",
    "업데이트",
    "다음",
)
DECISION_KEYWORDS = (
    "decide",
    "decision",
    "agreed",
    "conclusion",
    "결정",
    "합의",
    "확정",
    "정리하면",
    "결론",
    "우선",
    "일단",
)
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "have",
    "will",
    "been",
    "into",
    "about",
    "there",
    "they",
    "you",
    "your",
    "are",
    "was",
    "were",
    "우리",
    "저희",
    "그냥",
    "이제",
    "그리고",
    "근데",
    "그래서",
    "하면",
    "해서",
    "있는",
    "없는",
    "대한",
    "관련",
    "정도",
    "아마",
    "그거",
    "이거",
    "지금",
    "다음",
    "같은",
    "때문",
    "수정",
}

COMMON_ENGLISH_WORDS = {
    "hello",
    "good",
    "morning",
    "yeah",
    "okay",
    "right",
    "thing",
    "things",
    "also",
    "really",
    "just",
    "kind",
    "want",
    "need",
    "look",
    "take",
    "make",
    "done",
    "done",
    "time",
    "data",
    "code",
    "flow",
    "team",
    "project",
    "question",
    "questions",
    "speaker",
    "because",
    "there",
    "where",
    "which",
    "about",
    "would",
    "could",
    "should",
    "their",
    "these",
    "those",
    "while",
    "still",
    "again",
    "first",
    "second",
    "third",
    "something",
    "anything",
    "everything",
    "nothing",
}

MEETING_PHRASE_GLOSSARY: list[tuple[str, str]] = [
    ("sync up", "진행 상황을 맞추다"),
    ("loop in", "논의/메일에 포함시키다"),
    ("feel free to", "편하게 ~해도 된다"),
    ("regarding", "~에 관해서"),
    ("merged it into the repo", "레포에 머지했다"),
    ("running in the background", "백그라운드에서 실행 중이다"),
    ("as expected", "예상대로"),
    ("take into account", "~를 고려하다"),
    ("look into", "조사해 보다"),
    ("for now", "일단 지금은"),
    ("step by step", "단계적으로"),
    ("go from there", "그다음 단계로 진행하다"),
    ("at this point", "현 시점에서는"),
    ("for the sake of completeness", "완결성을 위해"),
    ("stick with", "~로 유지하다"),
    ("focus on", "~에 집중하다"),
    ("not a big deal", "큰 문제가 아니다"),
    ("narrow it down", "범위를 좁히다"),
    ("converge to", "~로 수렴하다"),
    ("keep it simple", "단순하게 가다"),
    ("follow up", "후속 조치/논의하다"),
    ("with respect to", "~에 대한/대비한"),
    ("to be expected", "예상 가능한"),
    ("range of interest", "관심 구간"),
    ("probability of detection", "검출 확률"),
    ("probability of false alarm", "오탐 확률"),
    ("root mean square error", "RMSE(제곱평균제곱근 오차)"),
]

MEETING_WORD_GLOSSARY: dict[str, str] = {
    "sparsity": "희소성",
    "refinement": "정제/고도화",
    "gating": "게이팅(연결 허용 기준)",
    "association": "연결/대응 매칭",
    "threshold": "임계값",
    "saturates": "포화된다",
    "resolution": "해상도",
    "robustness": "강건성",
    "empirical": "경험적",
    "holistically": "종합적으로",
    "beamwidth": "빔폭",
    "separability": "분리 가능성",
    "synthesized": "합성된",
    "integrated": "통합된",
    "calibrated": "보정된",
    "confluence": "Confluence 문서 시스템",
}

MEETING_ANOTHER_USAGE: dict[str, str] = {
    "sync up": "Let's sync up after the integration test and close the remaining issues.",
    "loop in": "Please loop in the QA team before sharing the final build.",
    "feel free to": "Feel free to ask for clarification if any requirement is ambiguous.",
    "regarding": "Regarding the release timeline, we need one more review cycle.",
    "merged it into the repo": "After code review, we merged it into the repo and tagged the release.",
    "running in the background": "A data export job is still running in the background.",
    "as expected": "The latency improved as expected after enabling caching.",
    "take into account": "We should take into account thermal drift in the final model.",
    "look into": "I'll look into the timeout issue and report back tomorrow.",
    "for now": "For now, let's keep the current threshold and monitor the trend.",
    "step by step": "Let's validate this step by step to avoid hidden regressions.",
    "go from there": "We can finalize the baseline first and go from there.",
    "at this point": "At this point, the risk is manageable with weekly monitoring.",
    "for the sake of completeness": "For the sake of completeness, include one edge-case scenario.",
    "stick with": "Let's stick with the simpler design until performance stabilizes.",
    "focus on": "We should focus on failure modes that affect customer impact.",
    "not a big deal": "This warning is not a big deal if retry logic is enabled.",
    "narrow it down": "We can narrow it down to two options after benchmark results.",
    "converge to": "The optimizer should converge to a stable minimum within 20 iterations.",
    "keep it simple": "Keep it simple for the first release, then iterate.",
    "follow up": "I'll follow up with the supplier on the missing test report.",
    "with respect to": "With respect to power usage, model B is more efficient.",
    "to be expected": "Minor variance at low SNR is to be expected.",
    "range of interest": "Most detections occur in the range of interest between 5 and 20 dB.",
    "probability of detection": "We track the probability of detection across different noise levels.",
    "probability of false alarm": "Reducing threshold too much increases the probability of false alarm.",
    "root mean square error": "Root mean square error dropped after recalibrating the sensor.",
}

PROPER_NOUN_EXCLUDE_TOKENS = {
    "gmsl",
    "matlab",
    "jetson",
    "nvidia",
    "oryx",
    "strata",
    "redis",
    "confluence",
    "graz",
}

TECHNICAL_ACRONYM_ALLOWLIST = {
    "pcb",
    "mcu",
    "rf",
    "rmse",
    "snr",
    "s11",
}

TECHNICAL_JARGON_EXCLUDE_TOKENS = {
    "snr",
    "rmse",
    "dml",
    "gmsl",
    "mcu",
    "pcb",
    "rf",
    "s11",
    "jetson",
    "matlab",
    "nvidia",
    "oryx",
    "deserializer",
    "serializer",
    "bootloader",
}

TECHNICAL_JARGON_EXCLUDE_PHRASES = {
    "monte carlo",
    "monte carlo simulation",
    "grid based dml",
    "grid-based dml",
    "point cloud",
    "signal processing",
    "streaming mode",
    "simulation result",
    "rf plate",
    "expansion plate",
    "shared mcu",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe audio files in a folder using AssemblyAI API."
    )
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--input-file",
        type=Path,
        default=None,
        help="Single audio/video file to transcribe.",
    )
    input_group.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Folder containing audio files (default: ./audio).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Folder for transcript files. "
            "If omitted with --input-file, outputs to G:/내 드라이브/Infineon/<input file name>. "
            "Otherwise defaults to ./transcripts."
        ),
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ASSEMBLYAI_API_KEY", ""),
        help="AssemblyAI API key. If omitted, uses ASSEMBLYAI_API_KEY env var.",
    )
    parser.add_argument(
        "--language",
        default="auto",
        help="Language code (default: auto). Use 'auto' for language detection.",
    )
    parser.add_argument(
        "--speech-model",
        choices=["universal-2", "universal-3-pro"],
        default="universal-3-pro",
        help="AssemblyAI speech model (default: universal-3-pro).",
    )
    parser.add_argument(
        "--custom-spelling-file",
        type=Path,
        default=None,
        help="Path to custom spelling JSON file.",
    )
    parser.add_argument(
        "--speaker-labels",
        action="store_true",
        help="Enable speaker diarization.",
    )
    parser.add_argument(
        "--no-speaker-labels",
        action="store_false",
        dest="speaker_labels",
        help="Disable speaker diarization.",
    )
    parser.set_defaults(speaker_labels=True)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing transcript files.",
    )
    parser.add_argument(
        "--summary-mode",
        choices=["auto", "llm"],
        default="auto",
        help="Meeting minutes generation mode (LLM-only).",
    )
    parser.add_argument(
        "--mom-detail",
        choices=["short", "normal", "long"],
        default="",
        help=(
            "Legacy global detail override for both KO/EN. "
            "If omitted, KO uses normal and EN uses normal."
        ),
    )
    parser.add_argument(
        "--mom-detail-ko",
        choices=["short", "normal", "long"],
        default="normal",
        help="Korean MoM detail level (default: normal).",
    )
    parser.add_argument(
        "--mom-detail-en",
        choices=["short", "normal", "long"],
        default="normal",
        help="English MoM detail level (default: normal).",
    )
    parser.add_argument(
        "--llm-api-key",
        default=os.environ.get("OPENAI_API_KEY", ""),
        help="OpenAI API key for LLM generation when --llm-provider=openai-api.",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["openclaw-agent", "openai-api"],
        default=os.environ.get("STT_FROM_MOM_LLM_PROVIDER", DEFAULT_LLM_PROVIDER),
        help=(
            "LLM backend provider. "
            "openclaw-agent uses OpenClaw session model/auth; openai-api calls OPENAI_BASE_URL directly."
        ),
    )
    parser.add_argument(
        "--llm-model",
        default=DEFAULT_LLM_MODEL,
        help=f"LLM model identifier (default: {DEFAULT_LLM_MODEL}).",
    )
    parser.add_argument(
        "--llm-base-url",
        default=os.environ.get("OPENAI_BASE_URL", DEFAULT_LLM_BASE_URL),
        help=f"OpenAI API base URL for --llm-provider=openai-api (default: {DEFAULT_LLM_BASE_URL}).",
    )
    parser.add_argument(
        "--openclaw-bin",
        default=os.environ.get("OPENCLAW_BIN", DEFAULT_OPENCLAW_BIN),
        help=f"OpenClaw CLI binary for --llm-provider=openclaw-agent (default: {DEFAULT_OPENCLAW_BIN}).",
    )
    parser.add_argument(
        "--openclaw-agent-id",
        default=DEFAULT_OPENCLAW_AGENT_ID,
        help=f"OpenClaw agent id for --llm-provider=openclaw-agent (default: {DEFAULT_OPENCLAW_AGENT_ID}).",
    )
    parser.add_argument(
        "--openclaw-session-prefix",
        default=DEFAULT_OPENCLAW_SESSION_PREFIX,
        help=f"Session id prefix for OpenClaw LLM calls (default: {DEFAULT_OPENCLAW_SESSION_PREFIX}).",
    )
    parser.add_argument(
        "--llm-thinking",
        choices=["off", "minimal", "low", "medium", "high"],
        default=DEFAULT_LLM_THINKING,
        help=f"Thinking level for --llm-provider=openclaw-agent (default: {DEFAULT_LLM_THINKING}).",
    )
    parser.add_argument(
        "--llm-timeout-seconds",
        type=int,
        default=120,
        help="LLM call timeout in seconds for meeting minutes/checklist/translation generation.",
    )
    parser.add_argument(
        "--meeting-english-max-items",
        type=int,
        default=14,
        help="Maximum number of checklist items in *_EnglishChecklist.md (default: 14).",
    )
    parser.add_argument(
        "--meeting-english-min-difficulty",
        type=int,
        default=4,
        help="Minimum difficulty score to keep checklist items (1-5, default: 4).",
    )
    parser.add_argument(
        "--meeting-english-allow-proper-nouns",
        action="store_true",
        help="Allow proper nouns/product names in checklist (default: excluded).",
    )
    parser.add_argument(
        "--meeting-english-allow-technical-terms",
        action="store_true",
        help="Allow technical/domain jargon terms (default: excluded).",
    )
    parser.add_argument(
        "--meeting-english-mode",
        choices=["auto", "extractive", "llm"],
        default="llm",
        help=(
            "Meeting English checklist mode. "
            "LLM errors are not auto-recovered; pipeline fails on checklist LLM failure."
        ),
    )
    parser.add_argument(
        "--meeting-english-llm-model",
        default=DEFAULT_MEETING_ENGLISH_LLM_MODEL,
        help=(
            "LLM model for *_EnglishChecklist.md when checklist mode uses LLM "
            f"(default: {DEFAULT_MEETING_ENGLISH_LLM_MODEL})."
        ),
    )
    parser.add_argument(
        "--meeting-english-vault-dir",
        type=Path,
        default=DEFAULT_MEETING_ENGLISH_VAULT_DIR,
        help=(
            "Secondary folder to copy *_EnglishChecklist.md. "
            f"(default: {DEFAULT_MEETING_ENGLISH_VAULT_DIR})"
        ),
    )
    parser.add_argument(
        "--no-meeting-english-vault-copy",
        action="store_true",
        help="Disable secondary copy of *_EnglishChecklist.md to vault folder.",
    )
    parser.add_argument(
        "--meeting-english-expressions-dir",
        type=Path,
        default=DEFAULT_MEETING_ENGLISH_EXPRESSIONS_DIR,
        help=(
            "Additional folder to copy *_EnglishChecklist.md for meeting expressions. "
            f"(default: {DEFAULT_MEETING_ENGLISH_EXPRESSIONS_DIR})"
        ),
    )
    parser.add_argument(
        "--no-meeting-english-expressions-copy",
        action="store_true",
        help="Disable additional copy of *_EnglishChecklist.md to meeting expressions folder.",
    )
    parser.add_argument(
        "--no-meeting-english-skip-checked",
        action="store_true",
        help=(
            "Do not exclude terms already marked as completed (- [x]) "
            "in existing *_EnglishChecklist.md files under "
            "--meeting-english-expressions-dir and --meeting-english-vault-dir."
        ),
    )
    parser.add_argument(
        "--mom-vault-dir",
        type=Path,
        default=DEFAULT_MOM_VAULT_DIR,
        help=(
            "Additional folder to copy *_MoM_KO.md and *_MoM_EN.md. "
            f"(default: {DEFAULT_MOM_VAULT_DIR})"
        ),
    )
    parser.add_argument(
        "--no-mom-vault-copy",
        action="store_true",
        help="Disable additional copy of MoM markdown files to mom vault folder.",
    )
    return parser.parse_args()


def _format_srt_time(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3_600_000
    total_ms %= 3_600_000
    minutes = total_ms // 60_000
    total_ms %= 60_000
    secs = total_ms // 1000
    ms = total_ms % 1000
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def _mom_profile(detail: str) -> dict[str, int | str]:
    profile = MOM_DETAIL_PROFILES.get(detail)
    if profile is None:
        return MOM_DETAIL_PROFILES["normal"]
    return profile


def _format_clock(seconds: float) -> str:
    total = int(round(seconds))
    hours = total // 3600
    total %= 3600
    minutes = total // 60
    secs = total % 60
    return f"{hours:02}:{minutes:02}:{secs:02}"


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_language_code(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", "_")


def _looks_like_korean_text(text: str) -> bool:
    if not text:
        return False
    hangul_chars = len(re.findall(r"[가-힣]", text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    return hangul_chars >= 12 and hangul_chars >= latin_chars


def _resolved_transcript_language_code(transcript: aai.Transcript, requested_language: str) -> str:
    raw = transcript.json_response or {}
    detected = _normalize_language_code(raw.get("language_code"))
    if detected:
        return detected

    requested = _normalize_language_code(requested_language)
    if requested and requested != "auto":
        return requested

    fallback_text = _normalize_space(transcript.text or "")
    if _looks_like_korean_text(fallback_text):
        return "ko"
    return ""


def _is_korean_language_code(language_code: str) -> bool:
    normalized = _normalize_language_code(language_code)
    return normalized == "ko" or normalized.startswith("ko_")


def _normalize_term_key(term: str) -> str:
    return _normalize_space(term).lower().replace("’", "'")


def _normalize_similarity_token(token: str) -> str:
    normalized = token.lower()
    if len(normalized) > 5 and normalized.endswith("ing"):
        normalized = normalized[:-3]
    elif len(normalized) > 4 and normalized.endswith("ed"):
        normalized = normalized[:-2]
    elif len(normalized) > 4 and normalized.endswith("es"):
        normalized = normalized[:-2]
    elif len(normalized) > 3 and normalized.endswith("s"):
        normalized = normalized[:-1]
    return normalized


def _term_similarity_tokens(term_key: str) -> set[str]:
    stop = {"the", "a", "an", "to", "of", "for", "and", "or", "in", "on", "with"}
    tokens = re.findall(r"[a-z0-9]+", term_key)
    normalized = {_normalize_similarity_token(token) for token in tokens if token not in stop}
    return {token for token in normalized if token}


def _is_similar_to_excluded(term_key: str, excluded_keys: set[str]) -> bool:
    if term_key in excluded_keys:
        return True

    term_tokens = _term_similarity_tokens(term_key)
    for excluded_key in excluded_keys:
        if term_key == excluded_key:
            return True

        # Strong substring overlap catches minor phrase variants.
        if len(term_key) >= 6 and len(excluded_key) >= 6:
            if term_key in excluded_key or excluded_key in term_key:
                return True

        excluded_tokens = _term_similarity_tokens(excluded_key)
        if term_tokens and excluded_tokens:
            shared = len(term_tokens & excluded_tokens)
            if shared > 0:
                union = len(term_tokens | excluded_tokens)
                if union > 0 and (shared / union) >= 0.67:
                    return True
                if min(len(term_tokens), len(excluded_tokens)) >= 2 and shared >= min(
                    len(term_tokens), len(excluded_tokens)
                ):
                    return True

        # Single-token near matches (e.g., segregate / segregation).
        if len(term_tokens) == 1 and len(excluded_tokens) == 1:
            token_a = next(iter(term_tokens))
            token_b = next(iter(excluded_tokens))
            if SequenceMatcher(None, token_a, token_b).ratio() >= 0.88:
                return True

    return False


def _collect_checked_terms_from_english_checklists(reference_dir: Path) -> set[str]:
    if not reference_dir.exists() or not reference_dir.is_dir():
        return set()

    checked_terms: set[str] = set()
    checklist_files = sorted(reference_dir.glob("*_EnglishChecklist.md"))
    pattern = re.compile(r"(?mi)^\s*-\s*\[[xX]\]\s+\*\*(.+?)\*\*")
    for checklist_path in checklist_files:
        try:
            content = checklist_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = checklist_path.read_text(encoding="utf-8", errors="ignore")
        for match in pattern.finditer(content):
            term_key = _normalize_term_key(match.group(1))
            if term_key:
                checked_terms.add(term_key)

    return checked_terms


def _term_regex(term: str) -> re.Pattern[str]:
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![A-Za-z]){escaped}(?![A-Za-z])", re.IGNORECASE)


def _find_example_line(context_lines: list[str], term: str) -> str:
    pattern = _term_regex(term)
    for line in context_lines:
        normalized = _normalize_space(line)
        if normalized and pattern.search(normalized):
            return _truncate_text(normalized, max_len=180)
    return ""


def _extract_meeting_english_items(
    *,
    full_text: str,
    context_lines: list[str],
    max_items: int,
    exclude_terms: set[str] | None = None,
) -> list[dict[str, str]]:
    normalized_text = _normalize_space(full_text)
    if not normalized_text:
        return []

    excluded = exclude_terms or set()
    items: list[dict[str, str]] = []
    seen_terms: set[str] = set()
    phrase_tokens_seen: set[str] = set()

    for term, meaning in MEETING_PHRASE_GLOSSARY:
        if len(items) >= max_items:
            break
        pattern = _term_regex(term)
        if not pattern.search(normalized_text):
            continue

        term_key = _normalize_term_key(term)
        if term_key in excluded:
            continue
        if term_key in seen_terms:
            continue
        seen_terms.add(term_key)
        phrase_tokens_seen.update(re.findall(r"[a-z]+", term_key))

        example = _find_example_line(context_lines, term) or term
        items.append(
            {
                "term": term,
                "kind": "phrase",
                "meaning": meaning,
                "example": example,
            }
        )

    if len(items) >= max_items:
        return items

    tokens = re.findall(r"[A-Za-z][A-Za-z\-]{5,}", normalized_text)
    token_counts = Counter(token.lower() for token in tokens)
    sorted_tokens = sorted(token_counts.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))

    for token, count in sorted_tokens:
        if len(items) >= max_items:
            break
        term_key = _normalize_term_key(token)
        if term_key in excluded:
            continue
        if term_key in seen_terms:
            continue
        if token in phrase_tokens_seen:
            continue
        if token in STOPWORDS or token in COMMON_ENGLISH_WORDS:
            continue
        if count < 2 and len(token) < 9:
            continue

        term = token
        meaning = MEETING_WORD_GLOSSARY.get(token, "회의 맥락에서 자주 나오는 단어 (뜻 확인)")
        example = _find_example_line(context_lines, term)
        if not example:
            continue

        seen_terms.add(term_key)
        items.append(
            {
                "term": term,
                "kind": "word",
                "meaning": meaning,
                "example": example,
            }
        )

    return items


def _meeting_english_checklist_path(base: Path) -> Path:
    return base.parent / f"{base.name}_EnglishChecklist.md"


def _another_usage_sentence(term: str, kind: str) -> str:
    key = term.lower()
    if key in MEETING_ANOTHER_USAGE:
        return MEETING_ANOTHER_USAGE[key]

    if kind == "phrase":
        if key.startswith("probability of "):
            return f"We analyze the {term} over multiple scenarios for fair comparison."
        if key.endswith(" to"):
            return f"We need to {key} a practical target before the next review."
        return f"We should use '{term}' consistently in the design review discussion."

    return f"The report uses '{term}' in a technical context, so it is worth memorizing."


def _build_meeting_english_checklist_markdown(
    *,
    src: Path,
    full_text: str,
    context_lines: list[str],
    max_items: int,
    exclude_terms: set[str] | None = None,
) -> str:
    items = _extract_meeting_english_items(
        full_text=full_text,
        context_lines=context_lines,
        max_items=max_items,
        exclude_terms=exclude_terms,
    )

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Meeting English Checklist ({src.stem})",
        "",
        f"- Generated at: {generated_at}",
        f"- Source file: `{src}`",
        f"- Total items: {len(items)}",
        "",
        "## Checklist",
        "",
    ]

    if not items:
        lines.append("- [ ] No expression was extracted automatically. Review transcript manually.")
        return "\n".join(lines) + "\n"

    for item in items:
        term = item["term"]
        kind = item["kind"]
        meaning = item["meaning"]
        example = item["example"].replace("|", "/")
        another_usage = _another_usage_sentence(term, kind).replace("|", "/")
        lines.append(f"- [ ] **{term}** ({kind}) - {meaning}")
        lines.append("  - Example during meeting")
        lines.append(f'    - "{example}"')
        lines.append("  - Another usage")
        lines.append(f"    - {another_usage}")
        lines.append("")

    return "\n".join(lines) + "\n"


def _filter_meeting_english_checklist_markdown(
    markdown: str,
    exclude_terms: set[str] | None,
    min_difficulty: int,
    exclude_proper_nouns: bool,
    exclude_technical_terms: bool,
) -> str:
    excluded = exclude_terms or set()
    if (
        not excluded
        and min_difficulty <= 1
        and not exclude_proper_nouns
        and not exclude_technical_terms
    ):
        return markdown

    lines = markdown.splitlines()
    item_pattern = re.compile(r"^\s*-\s*\[[ xX]\]\s+\*\*(.+?)\*\*")
    item_kind_pattern = re.compile(r"^\s*-\s*\[[ xX]\]\s+\*\*(.+?)\*\*\s+\(([^)]+)\)\s*-")
    difficulty_pattern = re.compile(r"\[D([1-5])\]", re.IGNORECASE)

    checklist_index = -1
    for idx, line in enumerate(lines):
        if line.strip().lower() == "## checklist":
            checklist_index = idx
            break
    if checklist_index < 0:
        return markdown

    header = lines[: checklist_index + 1]
    body = lines[checklist_index + 1 :]
    prefix_lines: list[str] = []
    blocks: list[tuple[str, str, str, int | None, list[str]]] = []

    def _looks_like_proper_noun_or_product(term: str) -> bool:
        tokens = re.findall(r"[A-Za-z0-9+\-]+", term)
        if not tokens:
            return False

        lowered_tokens = [token.lower() for token in tokens]
        if any(token in PROPER_NOUN_EXCLUDE_TOKENS for token in lowered_tokens):
            return True

        if len(tokens) == 1:
            token = tokens[0]
            lowered = token.lower()
            if re.fullmatch(r"[A-Z0-9]{3,}", token) and lowered not in TECHNICAL_ACRONYM_ALLOWLIST:
                return True
            if re.search(r"[A-Za-z]", token) and re.search(r"\d", token):
                return True
        return False

    def _looks_like_technical_jargon(term: str) -> bool:
        normalized = _normalize_term_key(term).replace("-", " ")
        if not normalized:
            return False
        if normalized in TECHNICAL_JARGON_EXCLUDE_PHRASES:
            return True
        if any(phrase in normalized for phrase in TECHNICAL_JARGON_EXCLUDE_PHRASES):
            return True

        tokens = re.findall(r"[a-z0-9]+", normalized)
        if any(token in TECHNICAL_JARGON_EXCLUDE_TOKENS for token in tokens):
            return True

        # Acronym-like uppercase metric/model terms are usually technical jargon.
        if re.search(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)?\b", term):
            lowered_tokens = {token.lower() for token in re.findall(r"[A-Za-z0-9]+", term)}
            if lowered_tokens & TECHNICAL_JARGON_EXCLUDE_TOKENS:
                return True
        return False

    def _is_item_line(value: str) -> bool:
        return bool(item_pattern.match(value))

    idx = 0
    while idx < len(body):
        line = body[idx]
        item_match = item_pattern.match(line)
        if not item_match:
            prefix_lines.append(line)
            idx += 1
            continue

        item_kind_match = item_kind_pattern.match(line)
        block = [line]
        idx += 1
        while idx < len(body) and not _is_item_line(body[idx]):
            block.append(body[idx])
            idx += 1
        raw_term = item_match.group(1).strip()
        kind_key = ""
        if item_kind_match:
            kind_key = _normalize_term_key(item_kind_match.group(2).strip())
        term_key = _normalize_term_key(raw_term)
        difficulty_match = difficulty_pattern.search(line)
        difficulty = int(difficulty_match.group(1)) if difficulty_match else None
        block[0] = difficulty_pattern.sub("", block[0]).replace("  ", " ").rstrip()
        blocks.append((raw_term, term_key, kind_key, difficulty, block))

    similar_excluded_keys = set(excluded)

    filtered_blocks = [
        block
        for raw_term, term_key, kind_key, difficulty, block in blocks
        if not _is_similar_to_excluded(term_key, similar_excluded_keys)
        and (kind_key == "phrasal verb" or difficulty is None or difficulty >= min_difficulty)
        and (not exclude_proper_nouns or not _looks_like_proper_noun_or_product(raw_term))
        and (not exclude_technical_terms or not _looks_like_technical_jargon(raw_term))
    ]

    total_line_found = False
    for i, line in enumerate(header):
        if re.match(r"^\s*-\s*Total items\s*:", line, flags=re.IGNORECASE):
            header[i] = f"- Total items: {len(filtered_blocks)}"
            total_line_found = True
            break

    if not total_line_found:
        insert_at = len(header)
        for i, line in enumerate(header):
            if line.strip().lower().startswith("- source file:"):
                insert_at = i + 1
                break
        header.insert(insert_at, f"- Total items: {len(filtered_blocks)}")

    if not filtered_blocks:
        rebuilt = (
            header
            + prefix_lines
            + [
                "",
                "- [ ] No expression was extracted automatically. Review transcript manually.",
            ]
        )
        return "\n".join(rebuilt).rstrip() + "\n"

    rebuilt_body = prefix_lines[:]
    for block in filtered_blocks:
        rebuilt_body.extend(block)
    rebuilt = header + rebuilt_body
    return "\n".join(rebuilt).rstrip() + "\n"


def _split_subtitle_text(text: str, max_chars: int = 84) -> list[str]:
    text = _normalize_space(text)
    if not text:
        return []

    chunks: list[str] = []
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(sentence) <= max_chars:
            chunks.append(sentence)
            continue

        clauses = re.split(r"(?<=[,;:，；：])\s+", sentence)
        for clause in clauses:
            clause = clause.strip()
            if not clause:
                continue

            if len(clause) <= max_chars:
                chunks.append(clause)
                continue

            words = clause.split()
            if len(words) <= 1:
                for i in range(0, len(clause), max_chars):
                    part = clause[i : i + max_chars].strip()
                    if part:
                        chunks.append(part)
                continue

            current: list[str] = []
            current_len = 0
            for word in words:
                add_len = len(word) + (1 if current else 0)
                if current and current_len + add_len > max_chars:
                    chunks.append(" ".join(current))
                    current = [word]
                    current_len = len(word)
                else:
                    current.append(word)
                    current_len += add_len
            if current:
                chunks.append(" ".join(current))

    return [chunk for chunk in chunks if chunk]


def _expand_subtitle_chunks(chunks: list[str], target_count: int) -> list[str]:
    if target_count <= len(chunks):
        return chunks

    expanded = list(chunks)
    while len(expanded) < target_count:
        idx = max(range(len(expanded)), key=lambda i: len(expanded[i]))
        text = expanded[idx]

        split_idx = text.rfind(" ", 0, len(text) // 2)
        if split_idx <= 0:
            split_idx = text.find(" ", len(text) // 2)
        if split_idx <= 0:
            break

        left = text[:split_idx].strip()
        right = text[split_idx + 1 :].strip()
        if not left or not right:
            break

        expanded[idx : idx + 1] = [left, right]

    return expanded


def _split_sentences(text: str) -> list[str]:
    cleaned = _normalize_space(text)
    if not cleaned:
        return []
    sentences = [s.strip() for s in re.split(r"(?<=[\.\?!。！？])\s+", cleaned) if s.strip()]
    if len(sentences) <= 1:
        sentences = [cleaned]
    return sentences


def _tokenize(sentence: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z]{2,}|[가-힣]{2,}", sentence)]


def _is_similar_sentence(a: str, b: str) -> bool:
    ta = {t for t in _tokenize(a) if t not in STOPWORDS}
    tb = {t for t in _tokenize(b) if t not in STOPWORDS}
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(len(ta), len(tb))
    return overlap >= 0.8


def _select_key_sentences(sentences: list[str], max_items: int) -> list[str]:
    if not sentences:
        return []

    freq: dict[str, int] = {}
    for sentence in sentences:
        unique_tokens = {t for t in _tokenize(sentence) if t not in STOPWORDS}
        for token in unique_tokens:
            freq[token] = freq.get(token, 0) + 1

    scored: list[tuple[float, int, str]] = []
    total = len(sentences)
    for idx, sentence in enumerate(sentences):
        tokens = [t for t in _tokenize(sentence) if t not in STOPWORDS]
        if not tokens:
            continue
        score = sum(freq.get(t, 0) for t in tokens) / (len(tokens) ** 0.5)
        position_bonus = 1.0 - (idx / max(total, 1)) * 0.15
        if len(sentence) > 240:
            score *= 0.9
        scored.append((score * position_bonus, idx, sentence))

    scored.sort(key=lambda item: item[0], reverse=True)

    selected: list[tuple[int, str]] = []
    for _, idx, sentence in scored:
        if len(sentence) < 20:
            continue
        if any(abs(idx - chosen_idx) <= 1 for chosen_idx, _ in selected):
            continue
        if any(_is_similar_sentence(sentence, existing) for _, existing in selected):
            continue
        selected.append((idx, sentence))
        if len(selected) >= max_items:
            break

    if not selected:
        selected = list(enumerate(sentences[:max_items]))

    selected.sort(key=lambda item: item[0])
    return [sentence for _, sentence in selected]


def _collect_speakers(transcript: aai.Transcript) -> list[str]:
    if not transcript.utterances:
        return []

    speakers: list[str] = []
    seen: set[str] = set()
    for utt in transcript.utterances:
        speaker = str(utt.dict().get("speaker") or "unknown").strip()
        if speaker in seen:
            continue
        seen.add(speaker)
        speakers.append(f"Speaker {speaker}")
    return speakers


def _truncate_text(text: str, max_len: int = 220) -> str:
    text = _normalize_space(text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _extract_keyword_items(
    transcript: aai.Transcript,
    keywords: tuple[str, ...],
    max_items: int,
) -> list[str]:
    if not transcript.utterances:
        return []

    items: list[str] = []
    seen: set[str] = set()
    lowered_keywords = tuple(k.lower() for k in keywords)

    for utt in transcript.utterances:
        data = utt.dict()
        text = _normalize_space(data.get("text") or "")
        if len(text) < 8:
            continue
        lower = text.lower()
        if not any(k in lower or k in text for k in lowered_keywords):
            continue

        speaker = data.get("speaker") or "unknown"
        start_seconds = (data.get("start", 0) or 0) / 1000.0
        line = f"[{_format_clock(start_seconds)}] Speaker {speaker}: {_truncate_text(text)}"
        if line in seen:
            continue
        seen.add(line)
        items.append(line)

        if len(items) >= max_items:
            break

    return items


def _format_duration(raw_duration: object) -> str:
    if isinstance(raw_duration, (int, float)):
        return _format_clock(float(raw_duration))
    return "-"


def mom_ko_path(base: Path) -> Path:
    return base.parent / f"{base.name}_MoM_KO.md"


def mom_en_path(base: Path) -> Path:
    return base.parent / f"{base.name}_MoM_EN.md"


def kr_subtitle_path(base: Path) -> Path:
    return base.parent / f"{base.name}_KR.srt"


def en_subtitle_path(base: Path) -> Path:
    return base.parent / f"{base.name}_EN.srt"


def _yaml_quote(value: str) -> str:
    # JSON string quoting is valid YAML double-quoted scalar syntax.
    return json.dumps(value, ensure_ascii=False)


def _normalize_frontmatter_source_file(markdown: str, source_file: str) -> str:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return markdown

    end_idx = -1
    for i in range(1, min(len(lines), 120)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        return markdown

    normalized_line = f"source_file: {_yaml_quote(source_file)}"
    for i in range(1, end_idx):
        if lines[i].lstrip().startswith("source_file:"):
            lines[i] = normalized_line
            return "\n".join(lines)

    insert_at = 1
    for i in range(1, end_idx):
        stripped = lines[i].lstrip()
        if stripped.startswith("generated_at:"):
            insert_at = i + 1
            break
        if stripped.startswith("title:"):
            insert_at = i + 1
    lines.insert(insert_at, normalized_line)
    return "\n".join(lines)


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _build_llm_source_text(transcript: aai.Transcript) -> tuple[str, bool]:
    if transcript.utterances:
        parts: list[str] = []
        for utt in transcript.utterances:
            data = utt.dict()
            text = _normalize_space(data.get("text") or "")
            if not text:
                continue
            speaker = data.get("speaker") or "unknown"
            start_seconds = (data.get("start", 0) or 0) / 1000.0
            parts.append(f"[{_format_clock(start_seconds)}] Speaker {speaker}: {text}")
        source = "\n".join(parts)
    else:
        source = _normalize_space(transcript.text or "")

    source = source.strip()
    truncated = False
    if len(source) > MAX_LLM_INPUT_CHARS:
        source = source[:MAX_LLM_INPUT_CHARS].rstrip() + "\n...[TRUNCATED]"
        truncated = True

    return source, truncated


def _call_openai_chat_completion(
    *,
    api_key: str,
    model: str,
    base_url: str,
    timeout_seconds: int,
    system_prompt: str,
    user_prompt: str,
) -> str:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    base_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    def _request_once(body: dict) -> str:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urlrequest.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with urlrequest.urlopen(request, timeout=timeout_seconds) as response:
                return response.read().decode("utf-8")
        except urlerror.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                detail = ""
            message = detail.strip() or str(exc.reason)
            raise RuntimeError(f"OpenAI API HTTP {exc.code}: {message}") from exc
        except urlerror.URLError as exc:
            raise RuntimeError(f"OpenAI API connection error: {exc.reason}") from exc

    body_with_temperature = dict(base_body)
    body_with_temperature["temperature"] = 0.2
    try:
        raw = _request_once(body_with_temperature)
    except RuntimeError as exc:
        # Some models (for example gpt-5-mini in chat/completions) reject custom temperature.
        # Retry once without temperature so LLM MoM generation still works instead of falling back.
        error_text = str(exc).lower()
        if "unsupported value" in error_text and "temperature" in error_text:
            raw = _request_once(base_body)
        else:
            raise

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenAI API returned non-JSON response.") from exc

    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("OpenAI API response does not include choices.")

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    text_parts.append(text_value)
        content = "\n".join(text_parts)

    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI API returned empty content.")

    return _strip_markdown_fence(content)


def _sanitize_session_component(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip())
    normalized = normalized.strip("-")
    return normalized or "stt-from-mom"


def _new_openclaw_session_id(prefix: str) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    safe_prefix = _sanitize_session_component(prefix)
    return f"{safe_prefix}-{stamp}-{uuid4().hex[:8]}"


def _extract_openclaw_payload_text(parsed: dict) -> str:
    result = parsed.get("result")
    if not isinstance(result, dict):
        return ""
    payloads = result.get("payloads")
    if not isinstance(payloads, list):
        return ""
    texts: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return "\n\n".join(texts).strip()


def _ensure_expected_model(agent_meta: dict, expected_model: str) -> None:
    expected = expected_model.strip()
    if not expected:
        return
    provider = str(agent_meta.get("provider") or "").strip()
    model = str(agent_meta.get("model") or "").strip()
    actual_full = f"{provider}/{model}" if provider and model else ""
    if expected and actual_full == expected:
        return
    if "/" not in expected and model == expected:
        return
    raise RuntimeError(
        "OpenClaw model mismatch. "
        f"Expected '{expected}', got '{actual_full or model or 'unknown'}'. "
        "Set your OpenClaw default model to the expected value and retry."
    )


def _call_openclaw_agent_completion(
    *,
    openclaw_bin: str,
    openclaw_agent_id: str,
    openclaw_session_prefix: str,
    model: str,
    thinking: str,
    timeout_seconds: int,
    system_prompt: str,
    user_prompt: str,
) -> str:
    session_id = _new_openclaw_session_id(openclaw_session_prefix)
    message = (
        "You are answering from an automation pipeline. Follow the instructions below exactly.\n\n"
        f"{system_prompt.strip()}\n\n"
        f"{user_prompt.strip()}"
    )
    cmd = [
        openclaw_bin,
        "agent",
        "--json",
        "--agent",
        openclaw_agent_id,
        "--session-id",
        session_id,
        "--thinking",
        thinking,
        "--message",
        message,
    ]
    completed = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(f"OpenClaw agent call failed: {detail}")

    raw = completed.stdout.strip()
    if not raw:
        raise RuntimeError("OpenClaw agent call returned empty output.")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenClaw agent call returned non-JSON output.") from exc

    status = str(parsed.get("status") or "").strip().lower()
    if status not in {"ok", "success"}:
        raise RuntimeError(f"OpenClaw agent status is not ok: {status or 'unknown'}")

    result = parsed.get("result")
    agent_meta = {}
    if isinstance(result, dict):
        meta = result.get("meta")
        if isinstance(meta, dict):
            maybe_agent_meta = meta.get("agentMeta")
            if isinstance(maybe_agent_meta, dict):
                agent_meta = maybe_agent_meta
    _ensure_expected_model(agent_meta, model)

    content = _extract_openclaw_payload_text(parsed)
    if not content:
        raise RuntimeError("OpenClaw agent returned empty text payload.")
    return _strip_markdown_fence(content)


def _call_llm_completion(
    *,
    llm_provider: str,
    api_key: str,
    model: str,
    base_url: str,
    openclaw_bin: str,
    openclaw_agent_id: str,
    openclaw_session_prefix: str,
    thinking: str,
    timeout_seconds: int,
    system_prompt: str,
    user_prompt: str,
) -> str:
    if llm_provider == "openai-api":
        return _call_openai_chat_completion(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    if llm_provider == "openclaw-agent":
        return _call_openclaw_agent_completion(
            openclaw_bin=openclaw_bin,
            openclaw_agent_id=openclaw_agent_id,
            openclaw_session_prefix=openclaw_session_prefix,
            model=model,
            thinking=thinking,
            timeout_seconds=timeout_seconds,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    raise ValueError(f"Unsupported llm_provider: {llm_provider}")


def write_mom_llm(
    path: Path,
    src: Path,
    transcript: aai.Transcript,
    *,
    llm_provider: str,
    api_key: str,
    model: str,
    base_url: str,
    openclaw_bin: str,
    openclaw_agent_id: str,
    openclaw_session_prefix: str,
    llm_thinking: str,
    timeout_seconds: int,
    output_language: str,
    mom_detail: str,
) -> None:
    if llm_provider == "openai-api" and not api_key:
        raise ValueError("LLM API key is required for llm summary mode.")
    if output_language not in {"ko", "en"}:
        raise ValueError("output_language must be 'ko' or 'en'.")

    source_text, truncated = _build_llm_source_text(transcript)
    if not source_text:
        raise ValueError("Transcript text is empty; cannot generate MoM.")
    profile = _mom_profile(mom_detail)
    key_points_target = int(profile["key_points"])
    decisions_target = int(profile["decisions"])
    actions_target = int(profile["actions"])
    ko_summary_hint = str(profile["ko_summary_hint"])
    en_summary_hint = str(profile["en_summary_hint"])
    llm_length_instruction = str(profile["llm_length_instruction"])

    raw = transcript.json_response or {}
    duration = _format_duration(raw.get("audio_duration"))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    source_file_for_yaml = str(src).replace("\\", "/")
    speakers = _collect_speakers(transcript)
    if output_language == "ko":
        speakers_line = ", ".join(speakers) if speakers else "미확인"
        truncated_note = (
            "참고: 전사본 길이 제한으로 일부 후반 내용이 잘렸습니다."
            if truncated
            else ""
        )
        system_prompt = (
            "You generate factual Korean meeting minutes in markdown from transcript text. "
            "Never invent facts. When uncertain, write '미확인'. Return markdown only."
        )
        user_prompt = f"""
다음 전사본을 기반으로 한국어 회의록을 작성하세요.

반드시 아래 형식을 사용:

---
title: {_yaml_quote(f"회의록 ({src.stem})")}
generated_at: {_yaml_quote(generated_at)}
source_file: {_yaml_quote(source_file_for_yaml)}
duration: {_yaml_quote(duration)}
speakers: {_yaml_quote(speakers_line)}
summary: >
  ... ({ko_summary_hint})
---

# 회의록 ({src.stem})

## 주요 논의
- ... (가능한 상세히, 목표 {key_points_target}개 내외)

## 결정 사항
- ... (목표 {decisions_target}개 내외)

## 액션 아이템
- [담당자] 할 일 (기한: ...) (목표 {actions_target}개 내외)

## 비고
- ...
- ...

규칙:
- 전사본에 없는 내용은 추측하지 마세요.
- 인명/제품명이 불명확하면 '미확인'으로 표기하세요.
- 액션 아이템은 발화 근거가 있을 때만 작성하세요.
- 회의 요약은 본문 섹션으로 만들지 말고 front matter의 `summary`에만 작성하세요.
- 헤딩 제목 앞에 번호(예: 1., 2.)를 붙이지 마세요.
- 출력은 순수 Markdown 본문만 주세요.
- 길이 가이드: {llm_length_instruction}
{truncated_note}

[전사본 시작]
{source_text}
[전사본 끝]
"""
    else:
        speakers_line = ", ".join(speakers) if speakers else "unknown"
        truncated_note = (
            "Note: The transcript was truncated due to input length limits."
            if truncated
            else ""
        )
        system_prompt = (
            "You generate factual English meeting minutes in markdown from transcript text. "
            "Never invent facts. When uncertain, write 'unknown'. Return markdown only."
        )
        user_prompt = f"""
Create English meeting minutes from the transcript below.

Use this exact structure:

---
title: {_yaml_quote(f"Meeting Minutes ({src.stem})")}
generated_at: {_yaml_quote(generated_at)}
source_file: {_yaml_quote(source_file_for_yaml)}
duration: {_yaml_quote(duration)}
speakers: {_yaml_quote(speakers_line)}
summary: >
  ... ({en_summary_hint})
---

# Meeting Minutes ({src.stem})

## Key Discussion Points
- ... (be detailed, target about {key_points_target} items)

## Decisions
- ... (target about {decisions_target} items)

## Action Items
- [Owner] Task (Due: ...) (target about {actions_target} items)

## Notes
- ...
- ...

Rules:
- Do not invent any facts that are not in the transcript.
- If names or terms are unclear, mark them as 'unknown'.
- Include action items only when there is transcript evidence.
- Put the executive summary only in front matter `summary`; do not create a separate summary section.
- Do not add numeric prefixes to headings.
- Return markdown only.
- Length guidance: {llm_length_instruction}
{truncated_note}

[Transcript Start]
{source_text}
[Transcript End]
"""

    content = _call_llm_completion(
        llm_provider=llm_provider,
        api_key=api_key,
        model=model,
        base_url=base_url,
        openclaw_bin=openclaw_bin,
        openclaw_agent_id=openclaw_agent_id,
        openclaw_session_prefix=openclaw_session_prefix,
        thinking=llm_thinking,
        timeout_seconds=timeout_seconds,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    content = _normalize_frontmatter_source_file(content, source_file_for_yaml)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def write_txt(path: Path, transcript: aai.Transcript) -> None:
    if transcript.utterances:
        lines = []
        for utt in transcript.utterances:
            data = utt.dict()
            text = (data.get("text") or "").strip()
            if not text:
                continue
            speaker = data.get("speaker") or "unknown"
            start_seconds = (data.get("start", 0) or 0) / 1000.0
            lines.append(f"[{_format_clock(start_seconds)}] Speaker {speaker}: {text}")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    text = transcript.text or ""
    path.write_text(text.strip(), encoding="utf-8")


def _parse_srt_timestamp(value: str) -> float:
    match = re.fullmatch(r"\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*", value)
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value!r}")
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = int(match.group(4))
    return (hours * 3600) + (minutes * 60) + seconds + (millis / 1000.0)


def _subtitle_entries_from_utterances(transcript: aai.Transcript) -> list[dict[str, float | str]]:
    entries: list[dict[str, float | str]] = []
    if not transcript.utterances:
        return entries

    for utt in transcript.utterances:
        data = utt.dict()
        text = _normalize_space(data.get("text") or "")
        if not text:
            continue

        speaker = data.get("speaker") or "unknown"
        start_seconds = (data.get("start", 0) or 0) / 1000.0
        end_seconds = (data.get("end", 0) or 0) / 1000.0
        if end_seconds <= start_seconds:
            continue

        duration = end_seconds - start_seconds
        chunks = _split_subtitle_text(text, max_chars=84)
        if not chunks:
            continue

        # Keep subtitle updates frequent even when diarization returns long turns.
        target_chunks = max(1, math.ceil(duration / 6.0))
        if len(chunks) < target_chunks:
            chunks = _expand_subtitle_chunks(chunks, target_chunks)

        weights = [max(1, len(c)) for c in chunks]
        total_weight = sum(weights)
        cumulative = 0

        for chunk_idx, chunk in enumerate(chunks):
            chunk_start = start_seconds + duration * (cumulative / total_weight)
            cumulative += weights[chunk_idx]
            if chunk_idx == len(chunks) - 1:
                chunk_end = end_seconds
            else:
                chunk_end = start_seconds + duration * (cumulative / total_weight)
                if chunk_end <= chunk_start:
                    chunk_end = min(end_seconds, chunk_start + 0.2)

            entries.append(
                {
                    "start": chunk_start,
                    "end": chunk_end,
                    "speaker": str(speaker),
                    "text": chunk,
                }
            )

    return entries


def _subtitle_entries_from_exported_srt(srt: str) -> list[dict[str, float | str]]:
    entries: list[dict[str, float | str]] = []
    for block in re.split(r"\r?\n\s*\r?\n", srt.strip()):
        raw_lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not raw_lines:
            continue

        time_line_index = 0
        if re.fullmatch(r"\d+", raw_lines[0]):
            time_line_index = 1

        if len(raw_lines) <= time_line_index + 1:
            continue

        time_line = raw_lines[time_line_index]
        if "-->" not in time_line:
            continue
        start_raw, end_raw = [part.strip() for part in time_line.split("-->", 1)]
        try:
            start_seconds = _parse_srt_timestamp(start_raw)
            end_seconds = _parse_srt_timestamp(end_raw)
        except ValueError:
            continue
        if end_seconds <= start_seconds:
            continue

        text = _normalize_space(" ".join(raw_lines[time_line_index + 1 :]))
        if not text:
            continue
        entries.append(
            {
                "start": start_seconds,
                "end": end_seconds,
                "speaker": "",
                "text": text,
            }
        )
    return entries


def _subtitle_entries_from_transcript(transcript: aai.Transcript) -> list[dict[str, float | str]]:
    if transcript.utterances:
        return _subtitle_entries_from_utterances(transcript)
    return _subtitle_entries_from_exported_srt(transcript.export_subtitles_srt())


def _subtitle_entry_text_for_display(entry: dict[str, float | str]) -> str:
    text = _normalize_space(str(entry.get("text", "")))
    if not text:
        return ""
    speaker = _normalize_space(str(entry.get("speaker", "")))
    if speaker:
        return f"Speaker {speaker}: {text}"
    return text


def _render_srt_entries(entries: list[dict[str, float | str]]) -> str:
    blocks: list[str] = []
    index = 1
    for entry in entries:
        text = _normalize_space(str(entry.get("text", "")))
        if not text:
            continue

        start_seconds = float(entry.get("start", 0.0) or 0.0)
        end_seconds = float(entry.get("end", 0.0) or 0.0)
        if end_seconds <= start_seconds:
            continue

        speaker = _normalize_space(str(entry.get("speaker", "")))
        if speaker:
            text = f"Speaker {speaker}: {text}"

        start = _format_srt_time(start_seconds)
        end = _format_srt_time(end_seconds)
        blocks.append(f"{index}\n{start} --> {end}\n{text}\n")
        index += 1
    return "\n".join(blocks)


def _render_bilingual_srt_entries(
    primary_entries: list[dict[str, float | str]],
    secondary_entries: list[dict[str, float | str]],
) -> str:
    blocks: list[str] = []
    max_count = max(len(primary_entries), len(secondary_entries))
    index = 1

    for i in range(max_count):
        primary = primary_entries[i] if i < len(primary_entries) else None
        secondary = secondary_entries[i] if i < len(secondary_entries) else None
        if primary is None and secondary is None:
            continue

        start_seconds = float((primary or secondary).get("start", 0.0) or 0.0)
        end_seconds = float((primary or secondary).get("end", 0.0) or 0.0)
        if end_seconds <= start_seconds:
            continue

        lines: list[str] = []
        if primary is not None:
            primary_text = _subtitle_entry_text_for_display(primary)
            if primary_text:
                lines.append(primary_text)
        if secondary is not None:
            secondary_text = _subtitle_entry_text_for_display(secondary)
            if secondary_text:
                lines.append(secondary_text)
        if not lines:
            continue

        start = _format_srt_time(start_seconds)
        end = _format_srt_time(end_seconds)
        blocks.append(f"{index}\n{start} --> {end}\n" + "\n".join(lines) + "\n")
        index += 1

    return "\n".join(blocks)


def write_srt(path: Path, transcript: aai.Transcript) -> None:
    if transcript.utterances:
        entries = _subtitle_entries_from_utterances(transcript)
        path.write_text(_render_srt_entries(entries), encoding="utf-8")
        return

    srt = transcript.export_subtitles_srt()
    path.write_text(srt, encoding="utf-8")


def _subtitle_translation_batches(
    entries: list[dict[str, float | str]],
    *,
    max_items: int = MAX_SUBTITLE_TRANSLATION_ITEMS_PER_BATCH,
    max_chars: int = MAX_SUBTITLE_TRANSLATION_CHARS_PER_BATCH,
) -> list[list[dict[str, float | str]]]:
    batches: list[list[dict[str, float | str]]] = []
    current: list[dict[str, float | str]] = []
    current_chars = 0

    for entry in entries:
        text = _normalize_space(str(entry.get("text", "")))
        if not text:
            continue
        item_chars = len(text)
        if current and (len(current) >= max_items or current_chars + item_chars > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(entry)
        current_chars += item_chars

    if current:
        batches.append(current)
    return batches


def _translate_subtitle_batch_to_english(
    entries: list[dict[str, float | str]],
    *,
    llm_provider: str,
    api_key: str,
    model: str,
    base_url: str,
    openclaw_bin: str,
    openclaw_agent_id: str,
    openclaw_session_prefix: str,
    llm_thinking: str,
    timeout_seconds: int,
) -> list[str]:
    payload_items = [
        {
            "id": idx + 1,
            "text": _normalize_space(str(entry.get("text", ""))),
        }
        for idx, entry in enumerate(entries)
    ]
    input_json = json.dumps(payload_items, ensure_ascii=False, indent=2)

    system_prompt = (
        "You translate subtitle lines into concise, natural English. "
        "Preserve meaning, numbers, units, and technical terms. "
        "Return strict JSON only."
    )
    user_prompt = f"""
Translate each input subtitle line into English.

Rules:
- Keep the same number of items and same ids.
- Do not add commentary.
- Keep each translation as a single subtitle line.
- Return JSON array only with objects: {{"id": <number>, "translation": "<text>"}}

Input JSON:
{input_json}
"""

    content = _call_llm_completion(
        llm_provider=llm_provider,
        api_key=api_key,
        model=model,
        base_url=base_url,
        openclaw_bin=openclaw_bin,
        openclaw_agent_id=openclaw_agent_id,
        openclaw_session_prefix=openclaw_session_prefix,
        thinking=llm_thinking,
        timeout_seconds=timeout_seconds,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Subtitle translation response is not valid JSON.") from exc

    rows: list[object] | None = None
    if isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict):
        for key in ("items", "translations", "results"):
            candidate = parsed.get(key)
            if isinstance(candidate, list):
                rows = candidate
                break
    if rows is None:
        raise RuntimeError("Subtitle translation response format is invalid.")

    translated_by_id: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("id")
        try:
            row_id = int(raw_id)
        except (TypeError, ValueError):
            continue

        translated_text = row.get("translation")
        if not isinstance(translated_text, str):
            fallback_text = row.get("text")
            translated_text = fallback_text if isinstance(fallback_text, str) else ""
        translated_text = _normalize_space(translated_text)
        if translated_text:
            translated_by_id[row_id] = translated_text

    translated_lines: list[str] = []
    for item in payload_items:
        row_id = int(item["id"])
        original = _normalize_space(str(item["text"]))
        translated_lines.append(translated_by_id.get(row_id, original))

    return translated_lines


def _build_translated_english_subtitle_entries(
    transcript: aai.Transcript,
    *,
    llm_provider: str,
    api_key: str,
    model: str,
    base_url: str,
    openclaw_bin: str,
    openclaw_agent_id: str,
    openclaw_session_prefix: str,
    llm_thinking: str,
    timeout_seconds: int,
) -> list[dict[str, float | str]]:
    source_entries = _subtitle_entries_from_transcript(transcript)
    if not source_entries:
        raise ValueError("Transcript does not contain subtitle entries for translation.")

    translated_entries: list[dict[str, float | str]] = []
    for batch in _subtitle_translation_batches(source_entries):
        translated_lines = _translate_subtitle_batch_to_english(
            batch,
            llm_provider=llm_provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            openclaw_bin=openclaw_bin,
            openclaw_agent_id=openclaw_agent_id,
            openclaw_session_prefix=openclaw_session_prefix,
            llm_thinking=llm_thinking,
            timeout_seconds=timeout_seconds,
        )
        for entry, translated_text in zip(batch, translated_lines):
            updated = dict(entry)
            updated["text"] = translated_text
            translated_entries.append(updated)
    return translated_entries


def write_translated_english_srt(
    path: Path,
    transcript: aai.Transcript,
    *,
    llm_provider: str,
    api_key: str,
    model: str,
    base_url: str,
    openclaw_bin: str,
    openclaw_agent_id: str,
    openclaw_session_prefix: str,
    llm_thinking: str,
    timeout_seconds: int,
) -> list[dict[str, float | str]]:
    if llm_provider == "openai-api" and not api_key:
        raise ValueError("LLM API key is required for English subtitle translation.")

    translated_entries = _build_translated_english_subtitle_entries(
        transcript,
        llm_provider=llm_provider,
        api_key=api_key,
        model=model,
        base_url=base_url,
        openclaw_bin=openclaw_bin,
        openclaw_agent_id=openclaw_agent_id,
        openclaw_session_prefix=openclaw_session_prefix,
        llm_thinking=llm_thinking,
        timeout_seconds=timeout_seconds,
    )
    path.write_text(_render_srt_entries(translated_entries), encoding="utf-8")
    return translated_entries


def write_bilingual_srt(
    path: Path,
    primary_entries: list[dict[str, float | str]],
    secondary_entries: list[dict[str, float | str]],
) -> None:
    path.write_text(
        _render_bilingual_srt_entries(primary_entries, secondary_entries),
        encoding="utf-8",
    )


def write_json(path: Path, src: Path, transcript: aai.Transcript) -> None:
    raw = transcript.json_response or {}

    words = []
    if transcript.words:
        for i, w in enumerate(transcript.words, start=1):
            wd = w.dict()
            words.append(
                {
                    "id": i,
                    "text": wd.get("text", ""),
                    "start": (wd.get("start", 0) or 0) / 1000.0,
                    "end": (wd.get("end", 0) or 0) / 1000.0,
                    "confidence": wd.get("confidence"),
                    "speaker": wd.get("speaker"),
                    "channel": wd.get("channel"),
                }
            )

    utterances = []
    if transcript.utterances:
        for i, u in enumerate(transcript.utterances, start=1):
            ud = u.dict()
            utterances.append(
                {
                    "id": i,
                    "text": ud.get("text", "").strip(),
                    "start": (ud.get("start", 0) or 0) / 1000.0,
                    "end": (ud.get("end", 0) or 0) / 1000.0,
                    "confidence": ud.get("confidence"),
                    "speaker": ud.get("speaker"),
                    "channel": ud.get("channel"),
                }
            )

    payload = {
        "source": str(src),
        "id": transcript.id,
        "status": str(transcript.status.value) if hasattr(transcript.status, "value") else str(transcript.status),
        "language_code": raw.get("language_code"),
        "confidence": raw.get("confidence"),
        "audio_duration": raw.get("audio_duration"),
        "text": transcript.text or "",
        "words": words,
        "utterances": utterances,
        "raw": raw,
    }

    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_meeting_english_checklist(
    path: Path,
    src: Path,
    transcript: aai.Transcript,
    max_items: int,
    exclude_terms: set[str] | None = None,
) -> str:
    if transcript.utterances:
        context_lines: list[str] = []
        for utt in transcript.utterances:
            data = utt.dict()
            text = _normalize_space(data.get("text") or "")
            if text:
                context_lines.append(text)
    else:
        context_lines = _split_sentences(transcript.text or "")

    full_text = _normalize_space(" ".join(context_lines)) if context_lines else _normalize_space(transcript.text or "")
    markdown = _build_meeting_english_checklist_markdown(
        src=src,
        full_text=full_text,
        context_lines=context_lines,
        max_items=max_items,
        exclude_terms=exclude_terms,
    )
    path.write_text(markdown, encoding="utf-8")
    return markdown


def write_meeting_english_checklist_llm(
    path: Path,
    src: Path,
    transcript: aai.Transcript,
    *,
    llm_provider: str,
    api_key: str,
    model: str,
    base_url: str,
    openclaw_bin: str,
    openclaw_agent_id: str,
    openclaw_session_prefix: str,
    llm_thinking: str,
    timeout_seconds: int,
    max_items: int,
    exclude_terms: set[str] | None = None,
    min_difficulty: int = 4,
    exclude_proper_nouns: bool = True,
    exclude_technical_terms: bool = True,
) -> str:
    if llm_provider == "openai-api" and not api_key:
        raise ValueError("LLM API key is required for LLM checklist mode.")

    source_text, truncated = _build_llm_source_text(transcript)
    if not source_text:
        raise ValueError("Transcript text is empty; cannot generate checklist.")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    excluded_list = sorted(exclude_terms or set())
    excluded_note = ""
    if excluded_list:
        capped = excluded_list[:300]
        excluded_lines = "\n".join(f"- {item}" for item in capped)
        if len(excluded_list) > len(capped):
            excluded_lines += f"\n- ... ({len(excluded_list) - len(capped)} more)"
        excluded_note = (
            "\nAlready marked completed terms in previous meeting checklists.\n"
            "Do NOT include any of these terms in the new checklist:\n"
            f"{excluded_lines}\n"
        )
    truncated_note = (
        "Note: transcript text was truncated due to input limits."
        if truncated
        else ""
    )
    system_prompt = (
        "You generate practical English-learning checklist markdown from meeting transcripts. "
        "Use only evidence from transcript for meeting examples. "
        "Do not invent meeting quotes. Return markdown only."
    )
    user_prompt = f"""
Create a markdown document with this exact structure and style:

# Meeting English Checklist ({src.stem})

- Generated at: {generated_at}
- Source file: `{src}`
- Total items: <actual_count>

## Checklist

For each item, use this exact template:
- [ ] **term** (phrase|phrasal verb|collocation|word) - Korean meaning [D3]
  - Example during meeting
    - "[HH:MM:SS] Speaker X: short quote from transcript"
  - Another usage
    - One natural sentence NOT copied from transcript
  - Korean learner note
    - One short tip: usage nuance OR common confusion point OR brief etymology

Checklist requirements:
- Generate up to {max_items} useful items.
- Quality over quantity: returning fewer high-value items is better than filling the count.
- Prioritize practical business/meeting expressions and phrasal verbs over generic words.
- If a useful phrasal verb appears in transcript, include it even when it seems easy.
- Internally score each candidate's learner difficulty from D1 (very easy) to D5 (advanced).
- Keep only items with D{min_difficulty} or higher.
- Prefer domain-specific terms that recur in the transcript (for example: segregation, granularity, deserializer, feasibility).
- Do NOT include simple proper nouns, person names, place names, or product/tool names (e.g., MATLAB, GMSL, Jetson, NVIDIA).
- Do NOT include deep technical subject-matter jargon, algorithm names, or metric labels (e.g., SNR, Monte Carlo Simulation, Grid-based DML).
- Keep "Example during meeting" quote short (max ~180 chars) and faithful to transcript.
- Another usage must reuse the same term naturally in a different context.
- Meanings should be concise Korean.
- Avoid duplicates.
- Skip items if transcript evidence is weak.
- Exclude terms already marked as completed in previous files.
- Exclude obvious variants/paraphrases of completed terms when possible.
- Return only markdown.
{truncated_note}
{excluded_note}

[Transcript Start]
{source_text}
[Transcript End]
"""
    content = _call_llm_completion(
        llm_provider=llm_provider,
        api_key=api_key,
        model=model,
        base_url=base_url,
        openclaw_bin=openclaw_bin,
        openclaw_agent_id=openclaw_agent_id,
        openclaw_session_prefix=openclaw_session_prefix,
        thinking=llm_thinking,
        timeout_seconds=timeout_seconds,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    markdown = _filter_meeting_english_checklist_markdown(
        content,
        exclude_terms,
        min_difficulty=min_difficulty,
        exclude_proper_nouns=exclude_proper_nouns,
        exclude_technical_terms=exclude_technical_terms,
    )
    path.write_text(markdown, encoding="utf-8")
    return markdown


def write_mom_extractive_ko(path: Path, src: Path, transcript: aai.Transcript, mom_detail: str) -> None:
    raw = transcript.json_response or {}
    profile = _mom_profile(mom_detail)
    full_text = _normalize_space(transcript.text or "")
    sentences = _split_sentences(full_text)

    overview_sentence_count = int(profile["overview_sentences"])
    overview_max_chars = int(profile["overview_max_chars"])
    key_points_max = int(profile["key_points"])
    decisions_max = int(profile["decisions"])
    actions_max = int(profile["actions"])

    overview = " ".join(sentences[:overview_sentence_count]).strip() if sentences else ""
    if not overview:
        overview = "자동 요약을 생성할 텍스트가 부족하여 원문 확인이 필요합니다."
    else:
        overview = _truncate_text(overview, max_len=overview_max_chars)

    key_points = _select_key_sentences(sentences, max_items=key_points_max)
    decisions = _extract_keyword_items(transcript, DECISION_KEYWORDS, max_items=decisions_max)
    actions = _extract_keyword_items(transcript, ACTION_KEYWORDS, max_items=actions_max)
    speakers = _collect_speakers(transcript)
    duration = _format_duration(raw.get("audio_duration"))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not key_points:
        key_points = [
            "핵심 논의 문장을 자동으로 충분히 추출하지 못했습니다. 원문(.txt/.srt) 확인이 필요합니다."
        ]
    if not decisions:
        decisions = ["명시적인 결정 문구를 자동 추출하지 못했습니다. 최종 검토 시 보완해 주세요."]
    if not actions:
        actions = ["명시적인 액션 아이템 문구를 자동 추출하지 못했습니다. 담당자와 기한을 확인해 주세요."]

    lines = [
        f"# 회의 요약 ({src.stem})",
        "",
        f"- 생성 시각: {generated_at}",
        f"- 원본 파일: `{src}`",
        f"- 길이: {duration}",
    ]
    if speakers:
        lines.append(f"- 화자: {', '.join(speakers)}")

    lines.extend(
        [
            "",
            "## 1. 한줄 요약",
            "",
            overview,
            "",
            "## 2. 주요 논의",
            "",
        ]
    )
    for idx, point in enumerate(key_points, start=1):
        lines.append(f"{idx}. {point}")

    lines.extend(["", "## 3. 결정 사항", ""])
    for item in decisions:
        lines.append(f"- {item}")

    lines.extend(["", "## 4. 액션 아이템", ""])
    for item in actions:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## 5. 비고",
            "",
            f"- 본 문서는 `{src.name}` STT 결과를 기반으로 자동 생성되었습니다.",
            "- 자동 생성 결과이므로 공유 전 수동 검토를 권장합니다.",
        ]
    )

    path.write_text("\n".join(lines), encoding="utf-8")


def write_mom_extractive_en(path: Path, src: Path, transcript: aai.Transcript, mom_detail: str) -> None:
    raw = transcript.json_response or {}
    profile = _mom_profile(mom_detail)
    full_text = _normalize_space(transcript.text or "")
    sentences = _split_sentences(full_text)

    overview_sentence_count = int(profile["overview_sentences"])
    overview_max_chars = int(profile["overview_max_chars"])
    key_points_max = int(profile["key_points"])
    decisions_max = int(profile["decisions"])
    actions_max = int(profile["actions"])

    overview = " ".join(sentences[:overview_sentence_count]).strip() if sentences else ""
    if not overview:
        overview = "Not enough transcript text to generate an automatic summary."
    else:
        overview = _truncate_text(overview, max_len=overview_max_chars)

    key_points = _select_key_sentences(sentences, max_items=key_points_max)
    decisions = _extract_keyword_items(transcript, DECISION_KEYWORDS, max_items=decisions_max)
    actions = _extract_keyword_items(transcript, ACTION_KEYWORDS, max_items=actions_max)
    speakers = _collect_speakers(transcript)
    duration = _format_duration(raw.get("audio_duration"))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not key_points:
        key_points = [
            "Key discussion sentences could not be extracted well. Please review the original transcript files."
        ]
    if not decisions:
        decisions = ["No explicit decision sentence was detected automatically. Review recommended."]
    if not actions:
        actions = ["No explicit action-item sentence was detected automatically. Please verify owner and due date."]

    lines = [
        f"# Meeting Minutes ({src.stem})",
        "",
        f"- Generated at: {generated_at}",
        f"- Source file: `{src}`",
        f"- Duration: {duration}",
    ]
    if speakers:
        lines.append(f"- Speakers: {', '.join(speakers)}")

    lines.extend(
        [
            "",
            "## 1. Executive Summary",
            "",
            overview,
            "",
            "## 2. Key Discussion Points",
            "",
        ]
    )
    for idx, point in enumerate(key_points, start=1):
        lines.append(f"{idx}. {point}")

    lines.extend(["", "## 3. Decisions", ""])
    for item in decisions:
        lines.append(f"- {item}")

    lines.extend(["", "## 4. Action Items", ""])
    for item in actions:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## 5. Notes",
            "",
            f"- This document was auto-generated from `{src.name}` STT output.",
            "- Manual review is recommended before sharing.",
        ]
    )

    path.write_text("\n".join(lines), encoding="utf-8")


def output_exists(base: Path) -> bool:
    return (
        base.with_suffix(".txt").exists()
        or base.with_suffix(".srt").exists()
        or base.with_suffix(".json").exists()
    )


def collect_input_files(input_dir: Path) -> list[Path]:
    candidates = [p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    by_stem: dict[str, dict[str, Path]] = {}
    for path in candidates:
        stem_map = by_stem.setdefault(path.stem, {})
        stem_map[path.suffix.lower()] = path

    selected: list[Path] = []
    for stem in sorted(by_stem):
        ext_map = by_stem[stem]
        chosen = None
        for ext in EXT_PRIORITY:
            if ext in ext_map:
                chosen = ext_map[ext]
                break
        if chosen is not None:
            selected.append(chosen)
    return selected


def load_custom_spelling(path: Path) -> dict[str, str | list[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Failed to read custom spelling JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("Custom spelling file must be a JSON object.")

    normalized: dict[str, str | list[str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Custom spelling keys must be non-empty strings.")

        if isinstance(value, str):
            normalized[key] = value
            continue

        if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
            normalized[key] = value
            continue

        raise ValueError(
            f"Invalid value for '{key}'. Use a string or list of non-empty strings."
        )

    return normalized


def build_config(
    language: str,
    speaker_labels: bool,
    speech_model: str,
    custom_spelling: dict[str, str | list[str]] | None,
) -> aai.TranscriptionConfig:
    normalized_language = _normalize_language_code(language)
    speech_models = [speech_model]
    # universal-3-pro does not support Korean directly; include universal-2 fallback
    # when language is auto-detect or explicitly Korean.
    if speech_model == "universal-3-pro" and (
        normalized_language == "auto" or normalized_language == "ko" or normalized_language.startswith("ko_")
    ):
        speech_models.append("universal-2")

    kwargs = {
        "speaker_labels": speaker_labels,
        "speech_models": speech_models,
        "punctuate": True,
        "format_text": True,
    }

    if normalized_language == "auto":
        kwargs["language_detection"] = True
    else:
        kwargs["language_code"] = language

    if custom_spelling:
        kwargs["custom_spelling"] = custom_spelling

    return aai.TranscriptionConfig(**kwargs)


def main() -> int:
    args = parse_args()
    if args.llm_timeout_seconds < 1:
        print("--llm-timeout-seconds must be >= 1.", file=sys.stderr)
        return 2
    if args.meeting_english_max_items < 1:
        print("--meeting-english-max-items must be >= 1.", file=sys.stderr)
        return 2
    if args.meeting_english_min_difficulty < 1 or args.meeting_english_min_difficulty > 5:
        print("--meeting-english-min-difficulty must be between 1 and 5.", file=sys.stderr)
        return 2

    if not args.api_key:
        print(
            "AssemblyAI API key not found. Set ASSEMBLYAI_API_KEY or pass --api-key.",
            file=sys.stderr,
        )
        return 2
    if args.llm_provider == "openai-api":
        if not args.llm_api_key:
            print(
                "Meeting minutes are LLM-only. Set OPENAI_API_KEY or pass --llm-api-key.",
                file=sys.stderr,
            )
            return 2
    elif args.llm_provider == "openclaw-agent":
        if not shutil.which(args.openclaw_bin):
            print(
                f"OpenClaw CLI not found: {args.openclaw_bin}. "
                "Install/open OpenClaw and ensure the binary is on PATH.",
                file=sys.stderr,
            )
            return 2
    else:
        print(f"Unsupported --llm-provider value: {args.llm_provider}", file=sys.stderr)
        return 2
    files: list[Path]

    if args.input_file is not None:
        src = args.input_file.expanduser().resolve()
        if not src.exists() or not src.is_file():
            print(f"Input file does not exist: {src}", file=sys.stderr)
            return 2
        if src.suffix.lower() not in SUPPORTED_EXTS:
            supported = ", ".join(sorted(SUPPORTED_EXTS))
            print(f"Unsupported input extension: {src.suffix}. Supported: {supported}", file=sys.stderr)
            return 2
        files = [src]
        output_dir = args.output_dir.resolve() if args.output_dir else (DEFAULT_OUTPUT_BASE_DIR / src.stem)
    else:
        input_dir = (args.input_dir or (Path.cwd() / "audio")).expanduser().resolve()
        if not input_dir.exists() or not input_dir.is_dir():
            print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
            return 2

        files = collect_input_files(input_dir)
        if not files:
            print(f"No supported audio files found in {input_dir}")
            return 1

        output_dir = args.output_dir.resolve() if args.output_dir else (Path.cwd() / "transcripts").resolve()

    output_dir.mkdir(parents=True, exist_ok=True)

    aai.settings.api_key = args.api_key
    transcriber = aai.Transcriber()
    custom_spelling: dict[str, str | list[str]] | None = None
    if args.custom_spelling_file:
        spelling_path = args.custom_spelling_file.resolve()
        if not spelling_path.exists():
            print(f"Custom spelling file does not exist: {spelling_path}", file=sys.stderr)
            return 2
        try:
            custom_spelling = load_custom_spelling(spelling_path)
            print(f"[INFO] Loaded custom spelling entries: {len(custom_spelling)}")
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    config = build_config(
        args.language,
        args.speaker_labels,
        args.speech_model,
        custom_spelling,
    )
    mom_detail_ko = args.mom_detail_ko
    mom_detail_en = args.mom_detail_en
    if args.mom_detail:
        mom_detail_ko = args.mom_detail
        mom_detail_en = args.mom_detail

    meeting_english_vault_dir = args.meeting_english_vault_dir.expanduser().resolve()
    meeting_english_expressions_dir = args.meeting_english_expressions_dir.expanduser().resolve()
    mom_vault_dir = args.mom_vault_dir.expanduser().resolve()
    checked_terms: set[str] = set()
    if not args.no_meeting_english_skip_checked:
        checked_reference_dirs = [meeting_english_expressions_dir, meeting_english_vault_dir]
        seen_reference_paths: set[Path] = set()
        total_loaded = 0
        for reference_dir in checked_reference_dirs:
            if reference_dir in seen_reference_paths:
                continue
            seen_reference_paths.add(reference_dir)
            loaded_terms = _collect_checked_terms_from_english_checklists(reference_dir)
            checked_terms.update(loaded_terms)
            total_loaded += len(loaded_terms)
            print(
                "[INFO] Loaded "
                f"{len(loaded_terms)} completed checklist term(s) from {reference_dir}"
            )
        print(
            "[INFO] Total completed checklist term(s) available for exclusion: "
            f"{len(checked_terms)} (raw loaded entries: {total_loaded})"
        )

    failures = 0

    for src in files:
        base_out = output_dir / src.stem
        if output_exists(base_out) and not args.overwrite:
            print(f"[SKIP] {src.name} already transcribed (use --overwrite)")
            continue

        try:
            transcript = transcriber.transcribe(str(src), config=config)
            if transcript.status == aai.TranscriptStatus.error:
                failures += 1
                print(f"[FAIL] {src.name}: {transcript.error}")
                continue

            write_txt(base_out.with_suffix(".txt"), transcript)
            write_json(base_out.with_suffix(".json"), src, transcript)
            transcript_language_code = _resolved_transcript_language_code(transcript, args.language)
            if transcript_language_code:
                print(f"[INFO] {src.name} transcript language: {transcript_language_code}")

            translated_en_srt_out: Path | None = None
            korean_srt_out: Path | None = None
            if _is_korean_language_code(transcript_language_code):
                korean_entries = _subtitle_entries_from_transcript(transcript)
                if not korean_entries:
                    raise ValueError("Transcript does not contain subtitle entries for Korean subtitles.")

                korean_srt_out = kr_subtitle_path(base_out)
                korean_srt_out.write_text(_render_srt_entries(korean_entries), encoding="utf-8")

                translated_en_srt_out = en_subtitle_path(base_out)
                translated_en_entries = write_translated_english_srt(
                    translated_en_srt_out,
                    transcript,
                    llm_provider=args.llm_provider,
                    api_key=args.llm_api_key,
                    model=args.llm_model,
                    base_url=args.llm_base_url,
                    openclaw_bin=args.openclaw_bin,
                    openclaw_agent_id=args.openclaw_agent_id,
                    openclaw_session_prefix=args.openclaw_session_prefix,
                    llm_thinking=args.llm_thinking,
                    timeout_seconds=args.llm_timeout_seconds,
                )
                write_bilingual_srt(
                    base_out.with_suffix(".srt"),
                    korean_entries,
                    translated_en_entries,
                )
                print(
                    "[INFO] Generated Korean subtitle source: "
                    f"{korean_srt_out.name}"
                )
                print(
                    "[INFO] Generated English subtitle translation: "
                    f"{translated_en_srt_out.name}"
                )
                print(
                    "[INFO] Generated bilingual subtitle (KR+EN): "
                    f"{base_out.with_suffix('.srt').name}"
                )
            else:
                write_srt(base_out.with_suffix(".srt"), transcript)

            english_checklist_out = _meeting_english_checklist_path(base_out)
            if args.meeting_english_mode == "extractive":
                english_markdown = write_meeting_english_checklist(
                    english_checklist_out,
                    src,
                    transcript,
                    max_items=args.meeting_english_max_items,
                    exclude_terms=checked_terms,
                )
                checklist_source = "extractive"
            else:
                english_markdown = write_meeting_english_checklist_llm(
                    english_checklist_out,
                    src,
                    transcript,
                    llm_provider=args.llm_provider,
                    api_key=args.llm_api_key,
                    model=args.meeting_english_llm_model,
                    base_url=args.llm_base_url,
                    openclaw_bin=args.openclaw_bin,
                    openclaw_agent_id=args.openclaw_agent_id,
                    openclaw_session_prefix=args.openclaw_session_prefix,
                    llm_thinking=args.llm_thinking,
                    timeout_seconds=args.llm_timeout_seconds,
                    max_items=args.meeting_english_max_items,
                    exclude_terms=checked_terms,
                    min_difficulty=args.meeting_english_min_difficulty,
                    exclude_proper_nouns=not args.meeting_english_allow_proper_nouns,
                    exclude_technical_terms=not args.meeting_english_allow_technical_terms,
                )
                checklist_source = f"{args.llm_provider}:{args.meeting_english_llm_model}"
            if not args.no_meeting_english_vault_copy:
                try:
                    meeting_english_vault_dir.mkdir(parents=True, exist_ok=True)
                    vault_out = meeting_english_vault_dir / english_checklist_out.name
                    vault_out.write_text(english_markdown, encoding="utf-8")
                except Exception as vault_exc:  # noqa: BLE001
                    print(f"[WARN] Failed to copy English checklist to vault: {vault_exc}")
            if not args.no_meeting_english_expressions_copy:
                try:
                    meeting_english_expressions_dir.mkdir(parents=True, exist_ok=True)
                    expressions_out = meeting_english_expressions_dir / english_checklist_out.name
                    expressions_out.write_text(english_markdown, encoding="utf-8")
                except Exception as expressions_exc:  # noqa: BLE001
                    print(f"[WARN] Failed to copy English checklist to expressions folder: {expressions_exc}")
            mom_ko_out = mom_ko_path(base_out)
            mom_en_out = mom_en_path(base_out)
            summary_source = f"{args.llm_provider}:{args.llm_model}"
            write_mom_llm(
                mom_ko_out,
                src,
                transcript,
                llm_provider=args.llm_provider,
                api_key=args.llm_api_key,
                model=args.llm_model,
                base_url=args.llm_base_url,
                openclaw_bin=args.openclaw_bin,
                openclaw_agent_id=args.openclaw_agent_id,
                openclaw_session_prefix=args.openclaw_session_prefix,
                llm_thinking=args.llm_thinking,
                timeout_seconds=args.llm_timeout_seconds,
                output_language="ko",
                mom_detail=mom_detail_ko,
            )
            write_mom_llm(
                mom_en_out,
                src,
                transcript,
                llm_provider=args.llm_provider,
                api_key=args.llm_api_key,
                model=args.llm_model,
                base_url=args.llm_base_url,
                openclaw_bin=args.openclaw_bin,
                openclaw_agent_id=args.openclaw_agent_id,
                openclaw_session_prefix=args.openclaw_session_prefix,
                llm_thinking=args.llm_thinking,
                timeout_seconds=args.llm_timeout_seconds,
                output_language="en",
                mom_detail=mom_detail_en,
            )
            if not args.no_mom_vault_copy:
                try:
                    mom_vault_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(mom_ko_out, mom_vault_dir / mom_ko_out.name)
                    shutil.copy2(mom_en_out, mom_vault_dir / mom_en_out.name)
                except Exception as mom_vault_exc:  # noqa: BLE001
                    print(f"[WARN] Failed to copy MoM files to mom vault folder: {mom_vault_exc}")

            output_suffix = ".txt/.srt/.json"
            if korean_srt_out is not None:
                output_suffix += "/_KR.srt"
            if translated_en_srt_out is not None:
                output_suffix += "/_EN.srt"
            print(
                f"[OK] {src.name} -> {base_out.name}{output_suffix}/_EnglishChecklist.md/_MoM_KO.md/_MoM_EN.md "
                f"(checklist={checklist_source}, summary={summary_source}, "
                f"detail_ko={mom_detail_ko}, detail_en={mom_detail_en})"
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[FAIL] {src.name}: {exc}")

    if failures:
        print(f"Finished with failures: {failures}")
        return 1

    print("All files transcribed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
