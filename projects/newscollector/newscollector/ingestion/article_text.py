from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

import httpx
from bs4 import BeautifulSoup, Tag

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class ArticleFetchResult:
    text: str
    error: str | None = None


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def _candidate_containers(soup: BeautifulSoup) -> list[Tag]:
    candidates: list[Tag] = []

    article_tags = soup.find_all("article")
    candidates.extend(article_tags)

    if soup.main:
        candidates.append(soup.main)

    hint = re.compile(
        r"(article|content|post|story|entry|main|body|detail|news)",
        flags=re.IGNORECASE,
    )
    for tag in soup.find_all(
        ["div", "section"], attrs={"class": hint}
    ) + soup.find_all(["div", "section"], attrs={"id": hint}):
        if isinstance(tag, Tag):
            candidates.append(tag)

    if soup.body:
        candidates.append(soup.body)

    deduped: list[Tag] = []
    seen: set[int] = set()
    for tag in candidates:
        key = id(tag)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(tag)
    return deduped


def _clean_container(tag: Tag) -> None:
    for junk in tag.find_all(
        ["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]
    ):
        junk.decompose()


def _paragraphs_from_container(tag: Tag) -> list[str]:
    paras: list[str] = []
    for p in tag.find_all(["p", "li"]):
        text = _normalize_whitespace(p.get_text(" ", strip=True))
        if len(text) >= 40:
            paras.append(text)
    return paras


def _best_text(candidates: Iterable[Tag]) -> str:
    best = ""
    for tag in candidates:
        cloned = BeautifulSoup(str(tag), "html.parser")
        node = cloned.find()
        if not isinstance(node, Tag):
            continue
        _clean_container(node)
        paras = _paragraphs_from_container(node)
        if not paras:
            continue
        merged = "\n".join(paras)
        if len(merged) > len(best):
            best = merged
    return best.strip()


def fetch_article_text(
    url: str,
    timeout_seconds: int = 20,
    max_retries: int = 1,
) -> ArticleFetchResult:
    last_error: str | None = None
    response: httpx.Response | None = None
    for _attempt in range(max(0, max_retries) + 1):
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
                timeout=timeout_seconds,
                follow_redirects=True,
            )
            break
        except Exception as exc:  # noqa: BLE001
            last_error = f"request_error: {exc}"

    if response is None:
        return ArticleFetchResult(text="", error=last_error or "request_error")

    if response.status_code != 200:
        return ArticleFetchResult(text="", error=f"http_{response.status_code}")

    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type and "xml" not in content_type:
        return ArticleFetchResult(text="", error=f"unsupported_content_type: {content_type}")

    soup = BeautifulSoup(response.text, "html.parser")
    text = _best_text(_candidate_containers(soup))
    if not text:
        return ArticleFetchResult(text="", error="no_article_text_extracted")

    # Keep artifacts manageable while retaining most of the source text.
    text = text[:120000].strip()
    return ArticleFetchResult(text=text, error=None)
