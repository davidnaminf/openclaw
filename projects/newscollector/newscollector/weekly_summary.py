from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

from newscollector.history_store import HistoryWindow
from newscollector.models import NewsItem

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency path
    OpenAI = None


COUNTRY_FLAG_MAP = {
    "KR": "🇰🇷",
    "CN": "🇨🇳",
    "US": "🇺🇸",
    "DE": "🇩🇪",
    "AT": "🇦🇹",
}

MARKET_RELEVANT_KEYWORDS = [
    "stock",
    "market",
    "earnings",
    "guidance",
    "inflation",
    "fed",
    "interest rate",
    "bond",
    "treasury",
    "ai",
    "semiconductor",
    "chip",
    "hbm",
    "nasdaq",
    "s&p",
    "dow",
    "korea",
    "kospi",
    "kosdaq",
    "etf",
    "반도체",
    "시장",
    "주식",
    "금리",
    "물가",
    "실적",
    "가이던스",
    "코스피",
    "코스닥",
    "환율",
    "수출",
]

ONE_OFF_KEYWORDS = [
    "fire",
    "dies",
    "died",
    "death",
    "killed",
    "shooting",
    "shotgun",
    "capitol police",
    "celebrity",
    "lakers",
    "sports",
    "interview",
    "crime",
    "화재",
    "사망",
    "총격",
    "사건",
    "사고",
    "연예",
    "스포츠",
    "경찰",
]

MODEL_PRICING_USD_PER_1M_TOKENS: Dict[str, Dict[str, float]] = {
    # Source: https://platform.openai.com/docs/pricing (standard text token rates)
    "gpt-5.2": {"input": 1.75, "cached_input": 0.175, "output": 14.0},
    "gpt-5.1": {"input": 1.25, "cached_input": 0.125, "output": 10.0},
    "gpt-5": {"input": 1.25, "cached_input": 0.125, "output": 10.0},
    "gpt-5-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.0},
    "gpt-5-nano": {"input": 0.05, "cached_input": 0.005, "output": 0.4},
}

MODEL_PRICING_ALIASES = {
    "gpt-5.2-chat-latest": "gpt-5.2",
    "gpt-5.1-chat-latest": "gpt-5.1",
    "gpt-5-chat-latest": "gpt-5",
    "gpt-5.2-codex": "gpt-5.2",
    "gpt-5.1-codex-max": "gpt-5.1",
    "gpt-5.1-codex": "gpt-5.1",
    "gpt-5-codex": "gpt-5",
}


@dataclass
class WeeklySummaryData:
    main_topic: str
    weather_line: str
    holiday_line: str
    tags: List[str]
    report_body: str


@dataclass
class LLMUsage:
    requested_model: str
    response_model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0
    estimated_cost_usd: float | None = None

    @property
    def model_for_display(self) -> str:
        return (self.response_model or self.requested_model).strip()


@dataclass
class RssSourceQuality:
    source: str
    total: int = 0
    market_relevant: int = 0
    one_off: int = 0

    @property
    def useful_ratio(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.market_relevant / self.total

    @property
    def one_off_ratio(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.one_off / self.total


def _yaml_quote(value: str) -> str:
    safe = (value or "").replace("\\", "\\\\").replace('"', '\\"')
    safe = safe.replace("\n", " ").strip()
    return f'"{safe}"'


def _frontmatter_key(line: str) -> str | None:
    match = re.match(r"^\s*([^:]+?)\s*:", line)
    if not match:
        return None
    return match.group(1).strip()


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _resolve_pricing_key(model_name: str) -> str | None:
    lowered = (model_name or "").strip().lower()
    if not lowered:
        return None
    lowered = MODEL_PRICING_ALIASES.get(lowered, lowered)
    if lowered in MODEL_PRICING_USD_PER_1M_TOKENS:
        return lowered
    for key in sorted(MODEL_PRICING_USD_PER_1M_TOKENS, key=len, reverse=True):
        if lowered.startswith(f"{key}-"):
            return key
    return None


def _estimate_cost_usd(
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_prompt_tokens: int,
) -> float | None:
    pricing_key = _resolve_pricing_key(model_name)
    if not pricing_key:
        return None
    pricing = MODEL_PRICING_USD_PER_1M_TOKENS[pricing_key]
    input_rate = float(pricing.get("input", 0.0) or 0.0)
    output_rate = float(pricing.get("output", 0.0) or 0.0)
    cached_input_rate = pricing.get("cached_input")
    cached_tokens = max(0, min(prompt_tokens, cached_prompt_tokens))
    non_cached_tokens = max(0, prompt_tokens - cached_tokens)

    input_cost = (non_cached_tokens / 1_000_000.0) * input_rate
    if cached_tokens > 0 and cached_input_rate is not None:
        input_cost += (cached_tokens / 1_000_000.0) * float(cached_input_rate)
    else:
        input_cost += (cached_tokens / 1_000_000.0) * input_rate
    output_cost = (max(0, completion_tokens) / 1_000_000.0) * output_rate
    return input_cost + output_cost


def _extract_llm_usage(completion: Any, requested_model: str) -> LLMUsage:
    usage = _obj_get(completion, "usage")
    prompt_tokens = _safe_int(_obj_get(usage, "prompt_tokens", 0))
    completion_tokens = _safe_int(_obj_get(usage, "completion_tokens", 0))
    total_tokens = _safe_int(_obj_get(usage, "total_tokens", 0))
    prompt_details = _obj_get(usage, "prompt_tokens_details")
    cached_prompt_tokens = _safe_int(_obj_get(prompt_details, "cached_tokens", 0))

    response_model = str(_obj_get(completion, "model", "") or "").strip()
    if total_tokens <= 0:
        total_tokens = max(0, prompt_tokens) + max(0, completion_tokens)

    estimated_cost_usd = _estimate_cost_usd(
        model_name=response_model or requested_model,
        prompt_tokens=max(0, prompt_tokens),
        completion_tokens=max(0, completion_tokens),
        cached_prompt_tokens=max(0, cached_prompt_tokens),
    )
    return LLMUsage(
        requested_model=requested_model,
        response_model=response_model,
        prompt_tokens=max(0, prompt_tokens),
        completion_tokens=max(0, completion_tokens),
        total_tokens=max(0, total_tokens),
        cached_prompt_tokens=max(0, cached_prompt_tokens),
        estimated_cost_usd=estimated_cost_usd,
    )


def _empty_llm_usage(model: str) -> LLMUsage:
    return LLMUsage(
        requested_model=model,
        estimated_cost_usd=_estimate_cost_usd(
            model_name=model,
            prompt_tokens=0,
            completion_tokens=0,
            cached_prompt_tokens=0,
        ),
    )


def _obsidian_wikilink(base_dir: Path, target_file: Path) -> str:
    try:
        rel = target_file.relative_to(base_dir)
    except ValueError:
        rel = Path(target_file.name)
    rel_posix = rel.as_posix()
    if rel_posix.lower().endswith(".md"):
        rel_posix = rel_posix[:-3]
    return f"[[{rel_posix}]]"


def _build_frontmatter_extras(
    generated_at: datetime,
    mode: str,
    model: str,
    llm_usage: LLMUsage,
    window: HistoryWindow,
    summary_output_dir: Path,
    raw_collection_md_path: Path | None,
    rss_quality_overview: str,
) -> List[str]:
    window_start = window.start_utc.date().isoformat()
    window_end = window.end_utc.date().isoformat()
    window_days = max((window.end_utc.date() - window.start_utc.date()).days, 1)
    lines = [
        f"Generated at: {_yaml_quote(generated_at.isoformat())}",
        f"Summary mode: {_yaml_quote(mode)}",
        f"Summary model: {_yaml_quote(llm_usage.model_for_display or model)}",
        f"LLM prompt tokens: {llm_usage.prompt_tokens}",
        f"LLM completion tokens: {llm_usage.completion_tokens}",
        f"LLM total tokens: {llm_usage.total_tokens}",
        f"LLM cached prompt tokens: {llm_usage.cached_prompt_tokens}",
        (
            f"LLM estimated cost (USD): {llm_usage.estimated_cost_usd:.8f}"
            if llm_usage.estimated_cost_usd is not None
            else f"LLM estimated cost (USD): {_yaml_quote('n/a')}"
        ),
        f"Window start: {window_start}",
        f"Window end: {window_end}",
        f"Window days: {window_days}",
        f"RSS count: {len(window.headlines)}",
        f"YouTube count: {len(window.youtube)}",
        f"Macro count: {len(window.macro)}",
        f"RSS quality: {_yaml_quote(rss_quality_overview)}",
    ]
    if raw_collection_md_path is not None:
        link = _obsidian_wikilink(summary_output_dir, raw_collection_md_path)
        lines.append(f"Raw collection: {_yaml_quote(link)}")
    return lines


def _inject_frontmatter_extras(
    lines: List[str],
    extra_lines: List[str],
) -> List[str]:
    if not lines or lines[0].strip() != "---":
        return lines

    end_idx = next(
        (idx for idx in range(1, len(lines)) if lines[idx].strip() == "---"),
        None,
    )
    if end_idx is None:
        return lines

    existing_keys: set[str] = set()
    for line in lines[1:end_idx]:
        key = _frontmatter_key(line)
        if key:
            existing_keys.add(key.lower())

    merged = lines[:end_idx]
    for line in extra_lines:
        key = _frontmatter_key(line)
        if not key:
            continue
        lowered = key.lower()
        if lowered in existing_keys:
            continue
        merged.append(line)
        existing_keys.add(lowered)

    merged.extend(lines[end_idx:])
    return merged


def _extract_frontmatter(lines: List[str]) -> List[str]:
    if not lines or lines[0].strip() != "---":
        return []
    end_idx = next(
        (idx for idx in range(1, len(lines)) if lines[idx].strip() == "---"),
        None,
    )
    if end_idx is None:
        return []
    return lines[: end_idx + 1]


def _compact(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _clean_template(text: str) -> List[str]:
    out: List[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not out:
            line = line.lstrip("\ufeff")
        stripped = line.lstrip()
        if stripped.startswith("%"):
            continue
        if "%" in line:
            line = line.split("%", 1)[0].rstrip()
        out.append(line)
    return out


def _country_with_flag(country_code: str) -> str:
    code = country_code.upper().strip()
    if not code:
        return ""
    flag = COUNTRY_FLAG_MAP.get(code, "")
    return f"{flag} {code}".strip()


def _extract_weather_line(macro_lines: List[str]) -> str:
    for line in macro_lines:
        if line.startswith("Weather/"):
            return line.split("|", 1)[1].strip() if "|" in line else line
    return ""


def _extract_today_holidays(macro_lines: List[str], today: date) -> str:
    today_iso = today.isoformat()
    rows: List[str] = []
    seen: set[str] = set()
    for line in macro_lines:
        if not line.startswith("Holiday/"):
            continue
        country = line.split("/", 1)[1].split("|", 1)[0].strip()
        country_with_flag = _country_with_flag(country)
        summary = line.split("|", 1)[1].strip() if "|" in line else ""
        for piece in summary.split(";"):
            piece = piece.strip()
            if not piece:
                continue
            match = re.match(r"(\d{4}-\d{2}-\d{2})\s+(.+)$", piece)
            if not match:
                continue
            if match.group(1) == today_iso:
                row = f"{country_with_flag}: {match.group(2).strip()}"
                if row in seen:
                    continue
                seen.add(row)
                rows.append(row)
    return "; ".join(rows)


def _build_evidence(
    window: HistoryWindow,
    max_rss_items: int,
    max_youtube_items: int,
) -> tuple[List[str], List[str], List[str]]:
    rss_lines: List[str] = []
    youtube_lines: List[str] = []
    macro_lines: List[str] = []

    for item in window.headlines[:max_rss_items]:
        context = item.summary or item.article_text
        rss_lines.append(
            f"{item.published_at.date().isoformat()} | {item.source} | "
            f"{item.title} | {_compact(context, 220)}"
        )
    for item in window.youtube[:max_youtube_items]:
        context = item.description or item.transcript_text
        youtube_lines.append(
            f"{item.published_at.date().isoformat()} | {item.channel_title} | "
            f"{item.title} | {_compact(context, 220)}"
        )
    for item in window.macro:
        macro_lines.append(f"{item.source} | {_compact(item.summary or item.title, 240)}")
    return rss_lines, youtube_lines, macro_lines


def _extract_json_object(text: str) -> Dict[str, object] | None:
    if not text.strip():
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    raw = fenced.group(1) if fenced else text.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict):
        return obj
    return None


def _build_llm_prompt(
    report_date: date,
    weather_line: str,
    holiday_line: str,
    rss_quality_overview: str,
    rss_lines: List[str],
    youtube_lines: List[str],
    macro_lines: List[str],
) -> str:
    return (
        "아래 최근 7일 데이터(RSS, 유튜브, 거시 컨텍스트)로 한국어 리포트를 작성해라.\n"
        "기존 템플릿의 고정 섹션 순서는 무시하고, 내용 흐름 중심으로 재배열해라.\n"
        "반드시 JSON 객체 하나만 출력하고, 키는 아래와 정확히 일치해야 한다.\n"
        "{\n"
        '  "main_topic": "한 문장",\n'
        '  "tags": ["태그1","태그2"],\n'
        '  "report_body": "마크다운 본문"\n'
        "}\n\n"
        "작성 규칙:\n"
        f"- 오늘 기준일은 {report_date.isoformat()}이다.\n"
        "- 지난 7일간 지속된 이슈를 먼저 정리하고, 오늘 해당 이슈가 어떻게 진전/변형됐는지 반드시 별도 소제목으로 작성한다.\n"
        "- 새롭게 발생한 이슈는 전망(상방/하방 또는 시나리오)까지 제시한다.\n"
        "- 단발성 사건성 뉴스는 생략하거나 1~2줄로 매우 간단히 언급한다.\n"
        "- 마크다운에서 H1/H2/H3와 bullet을 적극 사용한다.\n"
        "- 해딩에 1), 1. 같은 번호 표기는 사용하지 않는다.\n"
        "- 핵심 포인트에는 **중요** 표기를 넣는다.\n"
        "- # 개별 주 주식 시장 전망 아래에는 반드시 ## 한국, ## 미국/글로벌 하위 해딩을 둔다.\n"
        "- 종목명은 반드시 **종목명** 형태로 볼드 처리한다.\n"
        "- 투자 조언 단정 표현은 금지하고 리스크를 함께 제시한다.\n"
        "- report_body에는 최소한 아래 H1 제목을 포함한다:\n"
        "  # 뉴스 종합\n"
        "  # 한국 주식 시장 전망\n"
        "  # 미국 주식 시장 전망\n"
        "  # 개별 주 주식 시장 전망\n"
        "  # AI 의견\n\n"
        "[Today Context]\n"
        f"Weather: {weather_line or '(none)'}\n"
        f"Holiday: {holiday_line or '(none)'}\n"
        f"RSS quality overview: {rss_quality_overview}\n\n"
        "[RSS]\n"
        + ("\n".join(rss_lines) if rss_lines else "(none)")
        + "\n\n[YouTube]\n"
        + ("\n".join(youtube_lines) if youtube_lines else "(none)")
        + "\n\n[Macro]\n"
        + ("\n".join(macro_lines) if macro_lines else "(none)")
    )


def _extract_openclaw_usage(
    raw: Dict[str, object],
    requested_model: str,
) -> LLMUsage:
    result = raw.get("result")
    meta = result.get("meta") if isinstance(result, dict) else None
    agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else None

    usage = agent_meta.get("lastCallUsage") if isinstance(agent_meta, dict) else None
    if not isinstance(usage, dict):
        usage = agent_meta.get("usage") if isinstance(agent_meta, dict) else None
    if not isinstance(usage, dict):
        usage = {}

    prompt_tokens = _safe_int(usage.get("input", 0))
    completion_tokens = _safe_int(usage.get("output", 0))
    total_tokens = _safe_int(usage.get("total", 0))
    cached_prompt_tokens = _safe_int(usage.get("cacheRead", 0))

    response_model = ""
    if isinstance(agent_meta, dict):
        response_model = str(agent_meta.get("model", "") or "").strip()

    if total_tokens <= 0:
        total_tokens = max(0, prompt_tokens) + max(0, completion_tokens)

    estimated_cost_usd = _estimate_cost_usd(
        model_name=response_model or requested_model,
        prompt_tokens=max(0, prompt_tokens),
        completion_tokens=max(0, completion_tokens),
        cached_prompt_tokens=max(0, cached_prompt_tokens),
    )
    return LLMUsage(
        requested_model=requested_model,
        response_model=response_model,
        prompt_tokens=max(0, prompt_tokens),
        completion_tokens=max(0, completion_tokens),
        total_tokens=max(0, total_tokens),
        cached_prompt_tokens=max(0, cached_prompt_tokens),
        estimated_cost_usd=estimated_cost_usd,
    )


def _extract_openclaw_payload_text(raw: Dict[str, object]) -> str:
    result = raw.get("result")
    payloads = result.get("payloads") if isinstance(result, dict) else None
    if not isinstance(payloads, list):
        return ""
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        text = str(payload.get("text", "") or "").strip()
        if text:
            return text
    return ""


def _looks_market_relevant(item: NewsItem) -> bool:
    text = f"{item.title} {item.summary} {item.article_text[:500]}".lower()
    return any(keyword in text for keyword in MARKET_RELEVANT_KEYWORDS)


def _looks_one_off(item: NewsItem) -> bool:
    text = f"{item.title} {item.summary}".lower()
    return any(keyword in text for keyword in ONE_OFF_KEYWORDS)


def _build_rss_quality(window: HistoryWindow) -> List[RssSourceQuality]:
    rows: Dict[str, RssSourceQuality] = {}
    for item in window.headlines:
        source = item.source.strip() or "Unknown"
        row = rows.get(source)
        if row is None:
            row = RssSourceQuality(source=source)
            rows[source] = row
        row.total += 1
        if _looks_market_relevant(item):
            row.market_relevant += 1
        if _looks_one_off(item):
            row.one_off += 1

    return sorted(rows.values(), key=lambda x: (-x.total, x.source.lower()))


def _rss_quality_overview(stats: List[RssSourceQuality]) -> str:
    total = sum(x.total for x in stats)
    useful = sum(x.market_relevant for x in stats)
    one_off = sum(x.one_off for x in stats)
    if total <= 0:
        return "no RSS data"
    useful_ratio = useful / total * 100
    one_off_ratio = one_off / total * 100
    return (
        f"useful {useful_ratio:.1f}% ({useful}/{total}), "
        f"one-off {one_off_ratio:.1f}% ({one_off}/{total})"
    )


def _render_rss_quality_section(stats: List[RssSourceQuality]) -> str:
    lines = [
        "# RSS 소스 평가",
        "## 7일 데이터 품질 체크",
    ]
    if not stats:
        lines.append("- **중요** RSS 데이터가 없어 소스 평가를 수행하지 못했습니다.")
        return "\n".join(lines)

    total = sum(x.total for x in stats)
    useful = sum(x.market_relevant for x in stats)
    one_off = sum(x.one_off for x in stats)
    lines.append(
        "- **중요** 전체 RSS 대비 시장 연관 비율은 "
        f"**{(useful / max(total, 1)) * 100:.1f}%** 입니다."
    )
    lines.append(
        "- 단발성 비중은 "
        f"**{(one_off / max(total, 1)) * 100:.1f}%** 이며, 월간 교체 후보 판단 지표로 저장합니다."
    )
    lines.append("### 소스별 지표")
    for row in stats:
        lines.append(
            f"- {row.source}: 총 {row.total}건 / 시장 연관 {row.market_relevant}건 "
            f"({row.useful_ratio * 100:.1f}%) / 단발성 {row.one_off}건 "
            f"({row.one_off_ratio * 100:.1f}%)"
        )
    return "\n".join(lines)


def _ensure_rss_quality_section(report_body: str, rss_quality_section: str) -> str:
    if re.search(r"(?mi)^#\s*RSS\s*소스\s*평가\s*$", report_body or ""):
        return (report_body or "").strip()
    if not (report_body or "").strip():
        return rss_quality_section.strip()
    return f"{report_body.rstrip()}\n\n{rss_quality_section.strip()}"


def _section_bounds(lines: List[str], h1_title: str) -> tuple[int, int] | None:
    start = -1
    pattern = re.compile(rf"^#\s*{re.escape(h1_title)}\s*$", flags=re.IGNORECASE)
    for idx, line in enumerate(lines):
        if pattern.match(line.strip()):
            start = idx
            break
    if start < 0:
        return None

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].strip().startswith("# "):
            end = idx
            break
    return start, end


def _strip_heading_numbering(lines: List[str]) -> List[str]:
    out: List[str] = []
    for line in lines:
        normalized = re.sub(r"^(#{1,6}\s*)(\d+\s*[\.\)]\s*)", r"\1", line)
        out.append(normalized)
    return out


def _normalize_stock_subheadings(lines: List[str]) -> List[str]:
    bounds = _section_bounds(lines, "개별 주 주식 시장 전망")
    if bounds is None:
        return lines
    start, end = bounds

    block = lines[start + 1 : end]
    normalized_block: List[str] = []
    for line in block:
        stripped = line.strip()

        kr_match = re.match(r"^[-*]\s*한국\s*[:：]?\s*(.*)$", stripped)
        if kr_match:
            normalized_block.append("## 한국")
            rest = kr_match.group(1).strip()
            if rest:
                normalized_block.append(f"- {rest}")
            continue

        us_match = re.match(r"^[-*]\s*미국\s*/?\s*글로벌\s*[:：]?\s*(.*)$", stripped)
        if us_match:
            normalized_block.append("## 미국/글로벌")
            rest = us_match.group(1).strip()
            if rest:
                normalized_block.append(f"- {rest}")
            continue

        normalized_block.append(line)

    has_kr = any(re.match(r"^##\s*한국\s*$", x.strip()) for x in normalized_block)
    has_us = any(
        re.match(r"^##\s*미국\s*/?\s*글로벌\s*$", x.strip()) for x in normalized_block
    )

    if not has_kr:
        normalized_block.insert(0, "## 한국")

    if not has_us:
        insert_at = next(
            (
                i
                for i, line in enumerate(normalized_block)
                if "미국" in line or "글로벌" in line
            ),
            len(normalized_block),
        )
        normalized_block.insert(insert_at, "## 미국/글로벌")

    return lines[: start + 1] + normalized_block + lines[end:]


def _bold_stock_names(lines: List[str]) -> List[str]:
    bounds = _section_bounds(lines, "개별 주 주식 시장 전망")
    if bounds is None:
        return lines
    start, end = bounds

    generic_labels = {
        "중요",
        "시나리오",
        "상방",
        "하방",
        "리스크",
        "포인트",
        "체크포인트",
        "기준선",
        "업종 포인트",
        "수급/지수",
        "한국",
        "미국",
        "미국/글로벌",
        "글로벌",
    }

    output = lines[:]
    for idx in range(start + 1, end):
        line = output[idx]
        if not line.startswith("- ") or ":" not in line:
            continue
        if "(예:" in line:
            continue
        left, right = line[2:].split(":", 1)
        label = left.strip()
        if not label or label.startswith("**") and label.endswith("**"):
            continue
        if label in generic_labels:
            continue
        if "(" in label or ")" in label:
            continue
        if len(label) > 36:
            continue
        output[idx] = f"- **{label}**:{right}"
    return output


def _normalize_report_body(report_body: str) -> str:
    lines = (report_body or "").splitlines()
    lines = _strip_heading_numbering(lines)
    lines = _normalize_stock_subheadings(lines)
    lines = _bold_stock_names(lines)

    compacted: List[str] = []
    blank_count = 0
    for line in lines:
        if line.strip():
            blank_count = 0
            compacted.append(line.rstrip())
            continue
        blank_count += 1
        if blank_count <= 2:
            compacted.append("")
    return "\n".join(compacted).strip()


def _write_rss_quality_artifacts(
    rss_quality_dir: Path | None,
    report_date: date,
    generated_at: datetime,
    window: HistoryWindow,
    stats: List[RssSourceQuality],
) -> None:
    if rss_quality_dir is None:
        return

    rss_quality_dir.mkdir(parents=True, exist_ok=True)
    day_token = report_date.isoformat().replace("-", "")
    month_token = report_date.strftime("%Y%m")

    payload = {
        "report_date": report_date.isoformat(),
        "generated_at": generated_at.isoformat(),
        "window_start": window.start_utc.date().isoformat(),
        "window_end": window.end_utc.date().isoformat(),
        "sources": [asdict(row) for row in stats],
        "totals": {
            "total_items": sum(row.total for row in stats),
            "market_relevant_items": sum(row.market_relevant for row in stats),
            "one_off_items": sum(row.one_off for row in stats),
        },
    }
    day_path = rss_quality_dir / f"rss_quality_{day_token}.json"
    day_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    monthly_totals: Dict[str, RssSourceQuality] = {}
    days = 0
    for path in sorted(rss_quality_dir.glob(f"rss_quality_{month_token}[0-9][0-9].json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        source_rows = obj.get("sources", [])
        if not isinstance(source_rows, list):
            continue
        days += 1
        for raw in source_rows:
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("source", "")).strip() or "Unknown"
            row = monthly_totals.get(source)
            if row is None:
                row = RssSourceQuality(source=source)
                monthly_totals[source] = row
            row.total += int(raw.get("total", 0) or 0)
            row.market_relevant += int(raw.get("market_relevant", 0) or 0)
            row.one_off += int(raw.get("one_off", 0) or 0)

    monthly = {
        "month": f"{month_token[:4]}-{month_token[4:]}",
        "updated_at": generated_at.isoformat(),
        "days_recorded": days,
        "sources": [
            asdict(row)
            for row in sorted(
                monthly_totals.values(),
                key=lambda x: (-x.total, x.source.lower()),
            )
        ],
    }
    month_path = rss_quality_dir / f"rss_quality_{month_token}_summary.json"
    month_path.write_text(
        json.dumps(monthly, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _split_rss_lines_by_today(
    rss_lines: List[str],
    report_date: date,
) -> tuple[List[str], List[str]]:
    today_iso = report_date.isoformat()
    today_lines: List[str] = []
    history_lines: List[str] = []
    for line in rss_lines:
        head = line.split("|", 1)[0].strip()
        if head == today_iso:
            today_lines.append(line)
        else:
            history_lines.append(line)
    return today_lines, history_lines


def _default_summary(
    report_date: date,
    rss_lines: List[str],
    youtube_lines: List[str],
    weather_line: str,
    holiday_line: str,
) -> WeeklySummaryData:
    today_lines, history_lines = _split_rss_lines_by_today(rss_lines, report_date)
    ongoing = "\n".join(f"- {line}" for line in history_lines[:6]) or "- 7일 창구에서 뚜렷한 연속 이슈가 적었습니다."
    today_flow = "\n".join(f"- {line}" for line in today_lines[:6]) or "- 오늘자 핵심 업데이트가 제한적입니다."
    newly_rising = "\n".join(f"- {line}" for line in rss_lines[:4]) or "- 신규 이슈가 확인되지 않았습니다."
    youtube_refs = "\n".join(f"- {line}" for line in youtube_lines[:5]) or "- 참고할 유튜브 항목이 없습니다."

    macro_rows: List[str] = []
    if weather_line:
        macro_rows.append(f"- 서울 날씨: {weather_line}")
    if holiday_line:
        macro_rows.append(f"- 금일 휴일: {holiday_line}")
    if not macro_rows:
        macro_rows.append("- 금일 휴일/기상 특이사항 없음")

    body = (
        "# 뉴스 종합\n"
        "## 지난 7일 지속 이슈 흐름\n"
        f"{ongoing}\n\n"
        f"## 오늘 진행 상황 ({report_date.isoformat()})\n"
        f"{today_flow}\n\n"
        "## 새롭게 부상한 이슈와 전망\n"
        f"{newly_rising}\n\n"
        "## 단발성 이슈 메모\n"
        "- 단발성 사건성 기사(사고/연예/개별 사건)는 시장 영향이 제한적일 때 최소화해 반영합니다.\n\n"
        "# 한국 주식 시장 전망\n"
        "## 시나리오\n"
        "- **중요** 7일간 이어진 이슈가 오늘 수급과 변동성으로 어떻게 연결되는지 우선 점검합니다.\n"
        "- 상방: 실적/가이던스 개선과 AI 밸류체인 수요 확인.\n"
        "- 하방: 금리 재상승, 정책 불확실성, 단기 과열 조정.\n"
        "### 체크포인트\n"
        + "\n".join(macro_rows)
        + "\n\n"
        "# 미국 주식 시장 전망\n"
        "## 시나리오\n"
        "- **중요** AI 모멘텀 지속 여부와 금리 경로 재평가가 동시 작동하는 구간입니다.\n"
        "- 상방: 대형 기술주 실적 모멘텀 유지와 리스크 완화.\n"
        "- 하방: 밸류에이션 부담 확대와 크레딧 리스크 재부각.\n\n"
        "# 개별 주 주식 시장 전망\n"
        "## 한국\n"
        "- **삼성전자**: 반도체 업황 민감도가 높은 구간이며 실적/가이던스 확인이 우선입니다.\n"
        "- **SK하이닉스**: HBM 관련 수급 민감도가 높아 단기 변동성과 중기 추세가 공존합니다.\n"
        "## 미국/글로벌\n"
        "- **Nvidia**: AI 인프라 투자 모멘텀이 이어지지만 밸류에이션 부담도 동반됩니다.\n"
        "- **ARM**: 생태계 확장 기대와 실적 가시성 사이의 간극을 점검해야 합니다.\n"
        "- **Palo Alto Networks**: AI 전환 국면에서 소프트웨어 멀티플 재평가 영향을 받습니다.\n\n"
        "# 참고 소스 메모\n"
        "## YouTube\n"
        f"{youtube_refs}\n\n"
        "# AI 의견\n"
        "- **중요** 오늘 이슈를 단독 해석하기보다, 7일간 이어진 흐름의 연장선에서 해석하는 접근이 유효합니다.\n"
        "- 신규 이슈는 즉시 단정하지 말고 2~3일 후속 데이터로 검증하는 전략이 필요합니다."
    )
    return WeeklySummaryData(
        main_topic="7일 누적 흐름 기준으로 오늘의 진행 상황과 신규 이슈를 분리해 해석하는 장세",
        weather_line=weather_line,
        holiday_line=holiday_line,
        tags=["daily-summary", "weekly-window", "trend-first"],
        report_body=body,
    )


def _generate_with_openai(
    api_key: str,
    model: str,
    report_date: date,
    weather_line: str,
    holiday_line: str,
    rss_quality_overview: str,
    rss_lines: List[str],
    youtube_lines: List[str],
    macro_lines: List[str],
) -> tuple[WeeklySummaryData | None, LLMUsage]:
    if OpenAI is None:
        return None, _empty_llm_usage(model)
    client = OpenAI(api_key=api_key)
    prompt = _build_llm_prompt(
        report_date=report_date,
        weather_line=weather_line,
        holiday_line=holiday_line,
        rss_quality_overview=rss_quality_overview,
        rss_lines=rss_lines,
        youtube_lines=youtube_lines,
        macro_lines=macro_lines,
    )

    request = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        completion = client.chat.completions.create(
            **request,
            temperature=0.2,
        )
    except Exception as exc:
        # Some newer models only support default temperature.
        if "temperature" not in str(exc).lower():
            raise
        completion = client.chat.completions.create(**request)
    llm_usage = _extract_llm_usage(completion, requested_model=model)
    content = completion.choices[0].message.content or ""
    obj = _extract_json_object(content)
    if not obj:
        return None, llm_usage

    main_topic = str(obj.get("main_topic", "")).strip()
    report_body = str(obj.get("report_body", "")).strip()
    if not report_body:
        return None, llm_usage

    tags_raw = obj.get("tags", [])
    tags = (
        [str(tag).strip() for tag in tags_raw if str(tag).strip()]
        if isinstance(tags_raw, list)
        else []
    )
    if not main_topic:
        main_topic = "최근 7일 누적 이슈의 진행 경과와 오늘 변화를 중심으로 한 시장 점검"
    return (
        WeeklySummaryData(
            main_topic=main_topic,
            weather_line=weather_line,
            holiday_line=holiday_line,
            tags=(tags[:8] or ["daily-summary", "weekly-window", "trend-first"]),
            report_body=report_body,
        ),
        llm_usage,
    )


def _generate_with_openclaw(
    model: str,
    report_date: date,
    weather_line: str,
    holiday_line: str,
    rss_quality_overview: str,
    rss_lines: List[str],
    youtube_lines: List[str],
    macro_lines: List[str],
) -> tuple[WeeklySummaryData | None, LLMUsage]:
    openclaw_bin = shutil.which("openclaw")
    if not openclaw_bin:
        return None, _empty_llm_usage(model)

    prompt = _build_llm_prompt(
        report_date=report_date,
        weather_line=weather_line,
        holiday_line=holiday_line,
        rss_quality_overview=rss_quality_overview,
        rss_lines=rss_lines,
        youtube_lines=youtube_lines,
        macro_lines=macro_lines,
    )

    cmd = [
        openclaw_bin,
        "agent",
        "--agent",
        "main",
        "--thinking",
        "low",
        "--json",
        "--message",
        prompt,
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "unknown openclaw error"
        raise RuntimeError(f"openclaw agent failed: {detail}")

    output = (proc.stdout or "").strip()
    if not output:
        return None, _empty_llm_usage(model)

    raw = json.loads(output)
    if not isinstance(raw, dict):
        return None, _empty_llm_usage(model)

    llm_usage = _extract_openclaw_usage(raw, requested_model=model)
    content = _extract_openclaw_payload_text(raw)
    obj = _extract_json_object(content)
    if not obj:
        return None, llm_usage

    main_topic = str(obj.get("main_topic", "")).strip()
    report_body = str(obj.get("report_body", "")).strip()
    if not report_body:
        return None, llm_usage

    tags_raw = obj.get("tags", [])
    tags = (
        [str(tag).strip() for tag in tags_raw if str(tag).strip()]
        if isinstance(tags_raw, list)
        else []
    )
    if not main_topic:
        main_topic = "최근 7일 누적 이슈의 진행 경과와 오늘 변화를 중심으로 한 시장 점검"
    return (
        WeeklySummaryData(
            main_topic=main_topic,
            weather_line=weather_line,
            holiday_line=holiday_line,
            tags=(tags[:8] or ["daily-summary", "weekly-window", "trend-first"]),
            report_body=report_body,
        ),
        llm_usage,
    )


def _fill_frontmatter(
    line: str,
    report_date: date,
    summary: WeeklySummaryData,
    extra_values: Dict[str, str],
) -> str:
    stripped = line.strip()
    if re.match(r"^Date\s*:", stripped, flags=re.IGNORECASE):
        return f"Date: {report_date.isoformat()}"
    if re.match(r"^Main topic\s*:", stripped, flags=re.IGNORECASE):
        return f"Main topic: {_yaml_quote(summary.main_topic)}"
    if re.match(r"^Whether\s*:", stripped, flags=re.IGNORECASE):
        return f"Whether: {_yaml_quote(summary.weather_line)}"
    if re.match(r"^(Holyday|Holiday)\s*:", stripped, flags=re.IGNORECASE):
        key = stripped.split(":", 1)[0].strip()
        return f"{key}: {_yaml_quote(summary.holiday_line)}"
    if re.match(r"^tags\s*:", stripped, flags=re.IGNORECASE):
        tags = ", ".join(_yaml_quote(tag) for tag in summary.tags)
        return f"tags: [{tags}]"
    key = _frontmatter_key(line)
    if key:
        replaced = extra_values.get(key.lower())
        if replaced:
            return replaced
    return line


def render_weekly_summary_report(
    template_path: Path,
    output_dir: Path,
    window: HistoryWindow,
    report_date: date,
    holiday_reference_date: date | None,
    openai_api_key: str | None,
    model: str,
    max_rss_items: int,
    max_youtube_items: int,
    raw_collection_md_path: Path | None = None,
    generated_at: datetime | None = None,
    rss_quality_dir: Path | None = None,
) -> tuple[Path, str]:
    generated_at = generated_at or datetime.now()

    template_text = template_path.read_text(encoding="utf-8")
    cleaned_lines = _clean_template(template_text)
    rss_lines, youtube_lines, macro_lines = _build_evidence(
        window=window,
        max_rss_items=max_rss_items,
        max_youtube_items=max_youtube_items,
    )

    weather_line = _extract_weather_line(macro_lines)
    holiday_line = _extract_today_holidays(
        macro_lines,
        today=holiday_reference_date or report_date,
    )
    rss_quality_stats = _build_rss_quality(window)
    rss_quality_overview = _rss_quality_overview(rss_quality_stats)
    rss_quality_section = _render_rss_quality_section(rss_quality_stats)

    summary = None
    llm_usage = _empty_llm_usage(model)
    mode = "heuristic"
    try:
        summary, llm_usage = _generate_with_openclaw(
            model=model,
            report_date=report_date,
            weather_line=weather_line,
            holiday_line=holiday_line,
            rss_quality_overview=rss_quality_overview,
            rss_lines=rss_lines,
            youtube_lines=youtube_lines,
            macro_lines=macro_lines,
        )
        if summary:
            mode = "openclaw"
    except Exception:
        summary = None

    if openai_api_key:
        try:
            if summary is None:
                summary, llm_usage = _generate_with_openai(
                api_key=openai_api_key,
                model=model,
                report_date=report_date,
                weather_line=weather_line,
                holiday_line=holiday_line,
                rss_quality_overview=rss_quality_overview,
                rss_lines=rss_lines,
                youtube_lines=youtube_lines,
                macro_lines=macro_lines,
                )
                if summary:
                    mode = "openai"
        except Exception:  # pragma: no cover - network failures
            summary = None

    if not summary:
        summary = _default_summary(
            report_date=report_date,
            rss_lines=rss_lines,
            youtube_lines=youtube_lines,
            weather_line=weather_line,
            holiday_line=holiday_line,
        )

    summary.report_body = _ensure_rss_quality_section(
        report_body=summary.report_body,
        rss_quality_section=rss_quality_section,
    )
    summary.report_body = _normalize_report_body(summary.report_body)

    _write_rss_quality_artifacts(
        rss_quality_dir=rss_quality_dir,
        report_date=report_date,
        generated_at=generated_at,
        window=window,
        stats=rss_quality_stats,
    )

    extra_lines = _build_frontmatter_extras(
        generated_at=generated_at,
        mode=mode,
        model=model,
        llm_usage=llm_usage,
        window=window,
        summary_output_dir=output_dir,
        raw_collection_md_path=raw_collection_md_path,
        rss_quality_overview=rss_quality_overview,
    )
    extra_values: Dict[str, str] = {}
    for line in extra_lines:
        key = _frontmatter_key(line)
        if key:
            extra_values[key.lower()] = line
    cleaned_lines = _inject_frontmatter_extras(cleaned_lines, extra_lines)
    frontmatter_lines = _extract_frontmatter(cleaned_lines)

    rendered_lines: List[str] = []
    if frontmatter_lines:
        for line in frontmatter_lines:
            rendered_lines.append(
                _fill_frontmatter(
                    line,
                    report_date=report_date,
                    summary=summary,
                    extra_values=extra_values,
                )
            )
    else:
        rendered_lines.extend(
            [
                "---",
                f"Date: {report_date.isoformat()}",
                f'Main topic: {_yaml_quote(summary.main_topic)}',
                f'Whether: {_yaml_quote(summary.weather_line)}',
                f'Holiday: {_yaml_quote(summary.holiday_line)}',
                "tags: []",
            ]
        )
        rendered_lines.extend(extra_lines)
        rendered_lines.append("---")

    rendered_lines.append("")
    rendered_lines.extend(summary.report_body.strip().splitlines())

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"daily_summary_{report_date.isoformat().replace('-', '')}.md"
    out_path.write_text("\n".join(rendered_lines).rstrip() + "\n", encoding="utf-8")
    return out_path, mode
