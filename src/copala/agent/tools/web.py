"""Web tools: web_search and web_fetch."""

import html
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger

from copala.agent.tools.base import Tool

# Shared constants
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5  # Limit redirects to prevent DoS attacks


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL: must be http(s) with valid domain."""
    try:
        p = urlparse(url)
        if p.scheme not in ('http', 'https'):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        return True, ""
    except Exception as e:
        return False, str(e)


class WebSearchTool(Tool):
    """Search the web using Brave Search API or Tavily Search API."""

    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "description": "Results (1-10)", "minimum": 1, "maximum": 10}
        },
        "required": ["query"]
    }

    def __init__(
        self,
        api_key: str | None = None,
        max_results: int = 5,
        proxy: str | None = None,
        provider: str = "auto",
        api_base: str | None = None,
        search_depth: str = "basic",
        include_answer: bool = True,
    ):
        self._init_api_key = api_key
        self.max_results = max_results
        self.proxy = proxy
        self.provider = provider or "auto"
        self.api_base = (api_base or "").rstrip("/")
        self.search_depth = search_depth or "basic"
        self.include_answer = include_answer

    @property
    def api_key(self) -> str:
        """Resolve API key at call time so env/config changes are picked up."""
        return self._init_api_key or os.environ.get("TAVILY_API_KEY", "") or os.environ.get("BRAVE_API_KEY", "")

    @property
    def resolved_provider(self) -> str:
        """Resolve the configured search backend."""
        provider = self.provider.lower()
        if provider in {"brave", "tavily"}:
            return provider
        key = self.api_key
        if key.startswith("tvly-") or os.environ.get("TAVILY_API_KEY"):
            return "tavily"
        return "brave"

    async def execute(self, query: str, count: int | None = None, **kwargs: Any) -> str:
        if not self.api_key:
            return (
                "Error: web search API key not configured. Set tools.web.search.apiKey "
                "in ~/.copala/config.json, or export TAVILY_API_KEY / BRAVE_API_KEY, "
                "then restart the gateway."
            )

        if self.resolved_provider == "tavily":
            return await self._execute_tavily(query=query, count=count)

        return await self._execute_brave(query=query, count=count)

    async def _execute_brave(self, query: str, count: int | None = None) -> str:
        try:
            n = min(max(count or self.max_results, 1), 10)
            logger.debug("WebSearch: {}", "proxy enabled" if self.proxy else "direct connection")
            async with httpx.AsyncClient(proxy=self.proxy) as client:
                r = await client.get(
                    f"{self.api_base or 'https://api.search.brave.com'}/res/v1/web/search",
                    params={"q": query, "count": n},
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": self.api_key,
                        "Cache-Control": "no-cache"
                    },
                    timeout=10.0
                )
                r.raise_for_status()

            results = r.json().get("web", {}).get("results", [])[:n]
            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results, 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}")
                if desc := item.get("description"):
                    lines.append(f"   {desc}")
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            err_text = e.response.text
            status_code = e.response.status_code
            try:
                err_json = e.response.json()
                err_code = str(err_json.get("error", {}).get("code", ""))
                err_detail = str(err_json.get("error", {}).get("detail", ""))
            except Exception:
                err_code = ""
                err_detail = ""

            if err_code in {"SUBSCRIPTION_TOKEN_INVALID", "SUBSCRIPTION_TOKEN_DISABLED"}:
                logger.warning("WebSearch auth error: {} - {}", err_code, err_detail or status_code)
                return (
                    "Error: Brave Search API key is invalid. Please set a valid Brave Search API "
                    "subscription token in tools.web.search.apiKey, then restart the gateway."
                )
            if status_code in {401, 403}:
                logger.warning("WebSearch authorization failed: {} - {}", status_code, err_text)
                return (
                    "Error: Brave Search authorization failed. Please check tools.web.search.apiKey "
                    "and the Brave Search API subscription status."
                )

            logger.error("WebSearch HTTP error: {} - Response: {}", e, err_text)
            return f"Error: Web search request failed with HTTP {status_code}."
        except httpx.ProxyError as e:
            logger.error("WebSearch proxy error: {}", e)
            return f"Proxy error: {e}"
        except Exception as e:
            logger.error("WebSearch error: {}", e)
            return f"Error: {e}"

    async def _execute_tavily(self, query: str, count: int | None = None) -> str:
        try:
            n = min(max(count or self.max_results, 1), 10)
            logger.debug("WebSearch(Tavily): {}", "proxy enabled" if self.proxy else "direct connection")
            payload = {
                "query": query,
                "max_results": n,
                "search_depth": self.search_depth,
                "include_answer": self.include_answer,
                "include_raw_content": False,
            }
            async with httpx.AsyncClient(proxy=self.proxy) as client:
                r = await client.post(
                    f"{self.api_base or 'https://api.tavily.com'}/search",
                    json=payload,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    timeout=15.0,
                )
                r.raise_for_status()

            data = r.json()
            results = data.get("results", [])[:n]
            answer = str(data.get("answer") or "").strip()
            if not results and not answer:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            if answer:
                lines.append(f"Answer: {answer}")
            for i, item in enumerate(results, 1):
                title = item.get("title", "")
                url = item.get("url", "")
                content = item.get("content", "") or item.get("description", "")
                score = item.get("score")
                suffix = f" (score={score:.2f})" if isinstance(score, (int, float)) else ""
                lines.append(f"{i}. {title}{suffix}\n   {url}")
                if content:
                    lines.append(f"   {content}")
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            err_text = e.response.text
            status_code = e.response.status_code
            if status_code in {401, 403}:
                logger.warning("Tavily authorization failed: {} - {}", status_code, err_text)
                return (
                    "Error: Tavily Search authorization failed. Please check tools.web.search.apiKey "
                    "or TAVILY_API_KEY, then restart the gateway."
                )
            logger.error("Tavily Search HTTP error: {} - Response: {}", e, err_text)
            return f"Error: Tavily search request failed with HTTP {status_code}."
        except httpx.ProxyError as e:
            logger.error("Tavily Search proxy error: {}", e)
            return f"Proxy error: {e}"
        except Exception as e:
            logger.error("Tavily Search error: {}", e)
            return f"Error: {e}"


class WebFetchTool(Tool):
    """Fetch and extract content from a URL using Readability."""

    name = "web_fetch"
    description = "Fetch URL and extract readable content (HTML → markdown/text)."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "extractMode": {"type": "string", "enum": ["markdown", "text"], "default": "markdown"},
            "maxChars": {"type": "integer", "minimum": 100}
        },
        "required": ["url"]
    }

    def __init__(self, max_chars: int = 50000, proxy: str | None = None):
        self.max_chars = max_chars
        self.proxy = proxy

    async def execute(  # noqa: N803 - names are part of the public tool schema
        self,
        url: str,
        extractMode: str = "markdown",
        maxChars: int | None = None,
        **kwargs: Any,
    ) -> str:
        from readability import Document

        max_chars = maxChars or self.max_chars
        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            return json.dumps({"error": f"URL validation failed: {error_msg}", "url": url}, ensure_ascii=False)

        try:
            logger.debug("WebFetch: {}", "proxy enabled" if self.proxy else "direct connection")
            async with httpx.AsyncClient(
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                timeout=30.0,
                proxy=self.proxy,
            ) as client:
                r = await client.get(url, headers={"User-Agent": USER_AGENT})
                r.raise_for_status()

            ctype = r.headers.get("content-type", "")

            if "application/json" in ctype:
                text, extractor = json.dumps(r.json(), indent=2, ensure_ascii=False), "json"
            elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
                doc = Document(r.text)
                content = self._to_markdown(doc.summary()) if extractMode == "markdown" else _strip_tags(doc.summary())
                text = f"# {doc.title()}\n\n{content}" if doc.title() else content
                extractor = "readability"
            else:
                text, extractor = r.text, "raw"

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            return json.dumps({"url": url, "finalUrl": str(r.url), "status": r.status_code,
                              "extractor": extractor, "truncated": truncated, "length": len(text), "text": text}, ensure_ascii=False)
        except httpx.ProxyError as e:
            logger.error("WebFetch proxy error for {}: {}", url, e)
            return json.dumps({"error": f"Proxy error: {e}", "url": url}, ensure_ascii=False)
        except Exception as e:
            logger.error("WebFetch error for {}: {}", url, e)
            return json.dumps({"error": str(e), "url": url}, ensure_ascii=False)

    def _to_markdown(self, html: str) -> str:
        """Convert HTML to markdown."""
        # Convert links, headings, lists before stripping tags
        text = re.sub(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
                      lambda m: f'[{_strip_tags(m[2])}]({m[1]})', html, flags=re.I)
        text = re.sub(r'<h([1-6])[^>]*>([\s\S]*?)</h\1>',
                      lambda m: f'\n{"#" * int(m[1])} {_strip_tags(m[2])}\n', text, flags=re.I)
        text = re.sub(r'<li[^>]*>([\s\S]*?)</li>', lambda m: f'\n- {_strip_tags(m[1])}', text, flags=re.I)
        text = re.sub(r'</(p|div|section|article)>', '\n\n', text, flags=re.I)
        text = re.sub(r'<(br|hr)\s*/?>', '\n', text, flags=re.I)
        return _normalize(_strip_tags(text))
