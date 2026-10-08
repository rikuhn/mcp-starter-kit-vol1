"""
PDF Export MCP Server
=====================
Convert Claude's Markdown output to a clean, styled PDF, ready to share.
Uses WeasyPrint (headless, no browser needed). Falls back to an HTML file
when WeasyPrint or its native libraries are missing.

Safety: output is confined to PDF_OUTPUT_DIR, file names are sanitised,
and WeasyPrint may not load external resources (no file://, no network),
so a document cannot pull local files or call home.

Part of MCP Starter Kit Vol. 1 - https://github.com/rikuhn/mcp-starter-kit-vol1
License: MIT
"""

import html as html_lib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
from mcp.types import ToolAnnotations

try:
    import markdown as md_lib  # type: ignore
    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False

try:
    # WeasyPrint needs native libraries (pango, glib, cairo). When those are
    # missing it raises OSError - not ImportError - at import time, so we catch
    # broadly and fall back to HTML export instead of crashing the server.
    from weasyprint import HTML as WeasyprintHTML  # type: ignore
    WEASYPRINT_AVAILABLE = True
except Exception:
    WeasyprintHTML = None  # type: ignore
    WEASYPRINT_AVAILABLE = False

# ── Server ────────────────────────────────────────────────────────────────────
mcp = _Server("pdf_export_mcp")

OUTPUT_DIR = Path(os.environ.get("PDF_OUTPUT_DIR", str(Path.home() / "Desktop"))).expanduser()

# ── Default CSS (system fonts only - no web font download) ────────────────────
DEFAULT_CSS = """
@page {
    margin: 2.5cm;
    @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 9pt; color: #888; }
}
body { font-family: 'Helvetica Neue', 'Segoe UI', Arial, sans-serif; font-size: 11pt; line-height: 1.65; color: #1a1a2e; }
h1 { font-size: 22pt; font-weight: 700; color: #1a1a5e; margin-bottom: 0.3em; border-bottom: 2px solid #1a1a5e; padding-bottom: 6px; }
h2 { font-size: 16pt; font-weight: 600; color: #2a2a7e; margin-top: 1.4em; }
h3 { font-size: 13pt; font-weight: 600; color: #333; }
p  { margin: 0.6em 0; }
ul, ol { padding-left: 1.5em; }
li { margin: 0.3em 0; }
code { background: #f0f0f8; border: 1px solid #ddddf0; border-radius: 3px; padding: 1px 5px; font-family: 'Menlo', 'Consolas', monospace; font-size: 9.5pt; }
pre { background: #f5f5ff; border: 1px solid #ddddf0; border-left: 4px solid #6060c0; border-radius: 4px; padding: 1em; overflow-wrap: break-word; white-space: pre-wrap; }
pre code { background: none; border: none; padding: 0; }
blockquote { border-left: 4px solid #c0c0e0; margin: 1em 0; padding: 0.5em 1em; color: #555; background: #f8f8ff; }
table { width: 100%; border-collapse: collapse; margin: 1em 0; }
th { background: #1a1a5e; color: white; padding: 8px 12px; text-align: left; font-size: 10pt; }
td { padding: 7px 12px; border-bottom: 1px solid #ddd; font-size: 10pt; }
tr:nth-child(even) td { background: #f8f8ff; }
a { color: #3050c0; }
.document-meta { color: #888; font-size: 9pt; margin-bottom: 2em; }
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _no_external_fetcher():
    """URL fetcher that allows only inline data: URIs (works across WeasyPrint versions)."""
    try:  # WeasyPrint >= 66: class-based fetcher with a protocol allow-list
        from weasyprint.urls import URLFetcher  # type: ignore
        return URLFetcher(allowed_protocols={"data"})
    except ImportError:  # older WeasyPrint: plain callable
        from weasyprint import default_url_fetcher  # type: ignore

        def _fetch(url, *args, **kwargs):
            if url.startswith("data:"):
                return default_url_fetcher(url, *args, **kwargs)
            raise ValueError(f"External resources are disabled: {url[:80]}")
        return _fetch


def _safe_filename(name: str) -> str:
    """Reduce a requested file name to a single safe path component."""
    name = Path(name).name                       # drop any directory part
    name = re.sub(r"\.(pdf|html?)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^\w.\- ]", "_", name).strip(" .")
    if not name:
        raise ValueError("File name is empty after sanitising.")
    return name[:120]


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    """Never overwrite: append -2, -3, ... if the file exists."""
    candidate = directory / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def _markdown_to_html(text: str, title: str, author: str) -> str:
    if MARKDOWN_AVAILABLE:
        body = md_lib.markdown(text, extensions=["tables", "fenced_code", "toc", "nl2br"])
    else:
        body = "<p>" + html_lib.escape(text).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    safe_title = html_lib.escape(title)
    meta = f"{html_lib.escape(author)} · {ts}" if author else ts

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{safe_title}</title>
<style>{DEFAULT_CSS}</style>
</head>
<body>
<h1>{safe_title}</h1>
<div class="document-meta">{meta}</div>
{body}
</body>
</html>"""


def _fmt_error(e: Exception) -> str:
    return json.dumps({"error": str(e), "type": type(e).__name__})


# ── Input Models ──────────────────────────────────────────────────────────────

class PdfInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    content: str = Field(..., description="Markdown or plain text content to convert to PDF", min_length=1)
    filename: str = Field(..., description="Output file name without extension (e.g. 'Report_Q2_2026')", min_length=1, max_length=120)
    title: str = Field(default="Document", description="Document title displayed on the first page", max_length=200)
    author: str = Field(default="", description="Author name shown in the document header (optional)", max_length=120)


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="pdf_export",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
async def pdf_export(params: PdfInput) -> str:
    """Convert Markdown text to a styled PDF and save it in the output directory.

    Never overwrites an existing file. Falls back to saving HTML if WeasyPrint is unavailable.

    Args:
        params.content: Markdown or plain text.
        params.filename: Desired file name (no extension, no folders).
        params.title: Title shown in the document.
        params.author: Optional author name.

    Returns:
        str: JSON with output path and file size, or error.
    """
    try:
        stem = _safe_filename(params.filename)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        html = _markdown_to_html(params.content, params.title, params.author)

        if not WEASYPRINT_AVAILABLE:
            out_path = _unique_path(OUTPUT_DIR, stem, ".html")
            out_path.write_text(html, encoding="utf-8")
            return json.dumps({
                "success": True,
                "format": "html_fallback",
                "path": str(out_path),
                "note": "WeasyPrint not available - saved as HTML. See README > PDF export.",
            }, indent=2)

        out_path = _unique_path(OUTPUT_DIR, stem, ".pdf")
        WeasyprintHTML(string=html, url_fetcher=_no_external_fetcher()).write_pdf(str(out_path))
        size = out_path.stat().st_size
        return json.dumps({
            "success": True,
            "format": "pdf",
            "path": str(out_path),
            "size_bytes": size,
            "size_kb": round(size / 1024, 1),
        }, indent=2)
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(
    name="pdf_check_deps",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def pdf_check_deps() -> str:
    """Check whether all dependencies for PDF export are installed.

    Returns:
        str: JSON status of weasyprint and markdown libraries.
    """
    ready = WEASYPRINT_AVAILABLE and MARKDOWN_AVAILABLE
    return json.dumps({
        "weasyprint": WEASYPRINT_AVAILABLE,
        "markdown": MARKDOWN_AVAILABLE,
        "output_dir": str(OUTPUT_DIR),
        "ready": ready,
        "install_hint": "All good!" if ready else "pip install weasyprint markdown (macOS also: brew install pango)",
    }, indent=2)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[pdf_export_mcp] Output dir: {OUTPUT_DIR}", file=sys.stderr)
    print(f"  WeasyPrint: {'yes' if WEASYPRINT_AVAILABLE else 'no (HTML fallback)'}", file=sys.stderr)
    print(f"  Markdown:   {'yes' if MARKDOWN_AVAILABLE else 'no'}", file=sys.stderr)
    mcp.run()
