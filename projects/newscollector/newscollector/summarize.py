from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency path
    OpenAI = None


@dataclass
class SummaryResult:
    summary: str
    why_it_matters: str


class Summarizer(Protocol):
    def summarize(self, title: str, context: str) -> SummaryResult:
        ...


class HeuristicSummarizer:
    def summarize(self, title: str, context: str) -> SummaryResult:
        cleaned_context = _clean_text(context)
        summary = _first_sentences(cleaned_context, limit=220)
        if not summary:
            summary = _first_sentences(title, limit=180) or title
        why = _infer_impact(f"{title} {context}")
        return SummaryResult(summary=summary, why_it_matters=why)


class OpenAISummarizer:
    def __init__(self, api_key: str, model: str) -> None:
        if OpenAI is None:
            raise RuntimeError("openai package is not installed.")
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def summarize(self, title: str, context: str) -> SummaryResult:
        prompt = (
            "You are an analyst creating an overnight market report.\n"
            "Given title and context, return exactly two lines:\n"
            "SUMMARY: <= 35 words\n"
            "WHY: <= 25 words, explain market relevance.\n\n"
            f"TITLE: {title}\n"
            f"CONTEXT: {context[:3000]}"
        )
        request = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            completion = self.client.chat.completions.create(
                **request,
                temperature=0.2,
            )
        except Exception as exc:
            if "temperature" not in str(exc).lower():
                raise
            completion = self.client.chat.completions.create(**request)
        text = completion.choices[0].message.content or ""
        summary, why = _parse_labeled_output(text)
        if not summary:
            summary = _first_sentences(_clean_text(context), limit=220) or title
        if not why:
            why = _infer_impact(f"{title} {context}")
        return SummaryResult(summary=summary, why_it_matters=why)


def build_summarizer(api_key: str | None, model: str) -> Summarizer:
    if not api_key:
        return HeuristicSummarizer()
    try:
        return OpenAISummarizer(api_key=api_key, model=model)
    except Exception:
        return HeuristicSummarizer()


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _first_sentences(text: str, limit: int = 220) -> str:
    text = _clean_text(text)
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for part in parts:
        if not part:
            continue
        candidate = (out + " " + part).strip()
        if len(candidate) > limit and out:
            break
        out = candidate
        if len(out) >= limit:
            break
    return out[:limit].strip()


def _parse_labeled_output(text: str) -> tuple[str, str]:
    summary = ""
    why = ""
    for line in (text or "").splitlines():
        if line.upper().startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip()
        elif line.upper().startswith("WHY:"):
            why = line.split(":", 1)[1].strip()
    return summary, why


def _contains_keyword(text: str, token: str) -> bool:
    token = token.lower().strip()
    if not token:
        return False
    if " " in token:
        return token in text
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def _infer_impact(text: str) -> str:
    lowered = text.lower()
    if any(
        _contains_keyword(lowered, token)
        for token in ["rate", "fed", "inflation", "cpi", "ppi"]
    ):
        return "May change rate-cut expectations and cross-asset risk pricing."
    if any(
        _contains_keyword(lowered, token)
        for token in ["earnings", "guidance", "revenue", "profit"]
    ):
        return "Can reset valuation expectations for related sectors."
    if any(
        _contains_keyword(lowered, token)
        for token in ["ai", "semiconductor", "chip", "gpu"]
    ):
        return "Signals demand and capex trends in AI and semiconductor supply chains."
    return "Adds context for overnight risk sentiment and sector rotation."
