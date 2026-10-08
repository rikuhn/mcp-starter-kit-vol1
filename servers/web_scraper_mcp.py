"""
Web Scraper MCP Server
======================
Fetch any public URL and return clean Markdown, so Claude can read web
content without copy-paste.

Safety: only http/https, no private/loopback/link-local targets (SSRF guard),
every redirect hop is re-validated, responses are capped in size.

Part of MCP Starter Kit Vol. 1 - https://github.com/rikuhn/mcp-starter-kit-vol1
License: MIT
"""

import asyncio
import ipaddress
import json
import re
import socket
import sys
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
from mcp.types import ToolAnnotations

try:
    from markdownify import markdownify as md  # type: ignore
    MARKDOWNIFY_AVAILABLE = True
except ImportError:
    MARKDOWNIFY_AVAILABLE = False

# ── Server ────────────────────────────────────────────────────────────────────
mcp = _Server("web_scraper_mcp")

DEFAULT_TIMEOUT = 20.0
MAX_REDIRECTS = 5
MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB raw HTML is plenty for any article
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MCPStarterKit/1.1; +https://github.com/rikuhn/mcp-starter-kit-vol1)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
}

READ_ONLY_WEB = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown, falling back to basic tag stripping."""
    if MARKDOWNIFY_AVAILABLE:
        return md(html, heading_style="ATX", strip=["script", "style", "nav", "footer", "header"])
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s{2,}", "\n", text)
    return text.strip()


def _is_blocked_ip(ip: str) -> bool:
    """True if the IP is private, loopback, link-local, reserved or otherwise non-public."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable -> block to be safe
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped  # ::ffff:127.0.0.1 must be judged as 127.0.0.1
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local      # incl. 169.254.169.254 cloud metadata endpoint
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _validate_url(url: str) -> str:
    """Validate scheme/host and block SSRF targets (localhost, private/internal IPs, metadata endpoints)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs are supported, got: '{parsed.scheme}'")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Invalid URL - missing domain: '{url}'")

    host = hostname.lower().rstrip(".")
    if host in ("localhost", "localhost.localdomain") or host.endswith((".local", ".localhost", ".internal")):
        raise ValueError(f"Refusing to fetch internal/local address: '{hostname}'")

    try:
        ipaddress.ip_address(host)
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False
    if is_ip_literal:
        if _is_blocked_ip(host):
            raise ValueError(f"Refusing to fetch private/internal IP address: '{hostname}'")
        return url

    # Resolve and reject if ANY resolved address is non-public.
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        raise ValueError(f"Could not resolve host '{hostname}': {e}")
    for info in infos:
        ip = info[4][0]
        if _is_blocked_ip(ip):
            raise ValueError(f"Refusing to fetch '{hostname}' - resolves to internal address {ip}")
    return url


async def _safe_get(client: httpx.AsyncClient, url: str) -> tuple[str, str, str]:
    """GET with manual redirects (each hop re-validated) and a body size cap.

    Returns (final_url, content_type, text).
    """
    current = _validate_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        async with client.stream("GET", current, headers=DEFAULT_HEADERS) as resp:
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise ValueError(f"Redirect without Location header from {current}")
                current = _validate_url(urljoin(current, location))
                continue
            resp.raise_for_status()
            chunks, size = [], 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    raise ValueError(f"Response larger than {MAX_BODY_BYTES} bytes - aborted.")
                chunks.append(chunk)
            content_type = resp.headers.get("content-type", "")
            encoding = resp.charset_encoding or "utf-8"
            return current, content_type, b"".join(chunks).decode(encoding, errors="replace")
    raise ValueError(f"Too many redirects (>{MAX_REDIRECTS}) for {url}")


def _check_text_type(content_type: str) -> None:
    if content_type and "text" not in content_type and "html" not in content_type and "xml" not in content_type:
        raise ValueError(f"Unsupported content type: {content_type}. Only HTML/text pages are supported.")


def _fmt_error(e: Exception) -> str:
    return json.dumps({"error": str(e), "type": type(e).__name__})


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=False, timeout=DEFAULT_TIMEOUT)


# ── Input Models ──────────────────────────────────────────────────────────────

class FetchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    url: str = Field(..., description="Full URL to fetch (must start with http:// or https://)", min_length=10)
    max_chars: int = Field(default=8000, ge=500, le=50000, description="Maximum characters to return (default 8000)")
    raw_html: bool = Field(default=False, description="Return raw HTML instead of Markdown (default: false)")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_url(v)


class MultiInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    urls: list[str] = Field(..., description="List of URLs to fetch (max 5)", min_length=1, max_length=5)
    max_chars_each: int = Field(default=3000, ge=200, le=10000, description="Max characters per URL")

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, v: list[str]) -> list[str]:
        return [_validate_url(u.strip()) for u in v]


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool(name="web_fetch", annotations=READ_ONLY_WEB)
async def web_fetch(params: FetchInput) -> str:
    """Fetch a single public web page and return its content as clean Markdown.

    Strips navigation, scripts and styles. Ideal for reading articles,
    documentation or any public web page within Claude.

    Args:
        params.url: The HTTP/HTTPS URL to fetch.
        params.max_chars: Maximum characters to return (default 8000).
        params.raw_html: Set true to get raw HTML instead of Markdown.

    Returns:
        str: Page content as Markdown (or HTML), or a JSON error object.
    """
    try:
        async with _client() as client:
            _, content_type, body = await _safe_get(client, params.url)
        _check_text_type(content_type)
        if not params.raw_html:
            body = _html_to_markdown(body)
        if len(body) > params.max_chars:
            body = body[: params.max_chars] + f"\n\n... [truncated at {params.max_chars} chars]"
        return body
    except httpx.HTTPStatusError as e:
        return _fmt_error(ValueError(f"HTTP {e.response.status_code} for {params.url}"))
    except httpx.TimeoutException:
        return _fmt_error(TimeoutError(f"Request timed out after {DEFAULT_TIMEOUT}s for {params.url}"))
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(name="web_fetch_multi", annotations=READ_ONLY_WEB)
async def web_fetch_multi(params: MultiInput) -> str:
    """Fetch up to 5 public URLs concurrently and return all content in one response.

    Args:
        params.urls: List of HTTP/HTTPS URLs to fetch (max 5).
        params.max_chars_each: Max characters per URL (default 3000).

    Returns:
        str: JSON array with per-URL status and content.
    """
    async def _fetch_one(client: httpx.AsyncClient, url: str) -> dict:
        try:
            _, content_type, body = await _safe_get(client, url)
            _check_text_type(content_type)
            body = _html_to_markdown(body)
            if len(body) > params.max_chars_each:
                body = body[: params.max_chars_each] + "\n... [truncated]"
            return {"url": url, "status": "ok", "content": body}
        except Exception as e:
            return {"url": url, "status": "error", "error": str(e)}

    async with _client() as client:
        results = await asyncio.gather(*[_fetch_one(client, u) for u in params.urls])
    return json.dumps(list(results), indent=2, ensure_ascii=False)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("[web_scraper_mcp] Ready - fetches public URLs as Markdown.", file=sys.stderr)
    mcp.run()
