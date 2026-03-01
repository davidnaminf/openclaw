from newscollector.ingestion.article_text import fetch_article_text


class _FakeResponse:
    def __init__(self, status_code: int, text: str, content_type: str = "text/html"):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": content_type}


def test_fetch_article_text_extracts_article(monkeypatch):
    html = """
    <html><body>
      <article>
        <p>This is the first paragraph with enough text to pass the threshold for extraction.</p>
        <p>This is the second paragraph with enough text and useful details for testing output.</p>
      </article>
    </body></html>
    """

    def _fake_get(*args, **kwargs):
        return _FakeResponse(status_code=200, text=html)

    monkeypatch.setattr("newscollector.ingestion.article_text.httpx.get", _fake_get)
    result = fetch_article_text("https://example.com/article")

    assert result.error is None
    assert "first paragraph" in result.text
    assert "second paragraph" in result.text


def test_fetch_article_text_http_error(monkeypatch):
    def _fake_get(*args, **kwargs):
        return _FakeResponse(status_code=403, text="forbidden")

    monkeypatch.setattr("newscollector.ingestion.article_text.httpx.get", _fake_get)
    result = fetch_article_text("https://example.com/article")
    assert result.text == ""
    assert result.error == "http_403"
