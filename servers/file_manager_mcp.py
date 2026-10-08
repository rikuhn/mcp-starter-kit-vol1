"""
File Manager MCP Server
=======================
Read, write, list and search local files via Claude.
All operations are sandboxed to one root directory (FILE_MANAGER_ROOT).

Part of MCP Starter Kit Vol. 1 - https://github.com/rikuhn/mcp-starter-kit-vol1
License: MIT
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

# ── Server ────────────────────────────────────────────────────────────────────
mcp = _Server("file_manager_mcp")

# ── Safety: configurable root directory (resolved once, symlinks included) ────
ROOT_DIR = Path(os.environ.get("FILE_MANAGER_ROOT", str(Path.home() / "Documents"))).expanduser().resolve()

MAX_READ_BYTES = int(os.environ.get("FILE_MANAGER_MAX_READ_BYTES", str(5 * 1024 * 1024)))  # 5 MB
MAX_LIST_ENTRIES = 2000

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _inside_root(p: Path) -> bool:
    """True if the fully resolved path lies inside ROOT_DIR.

    Uses Path.relative_to instead of a string prefix check, so a sibling such as
    '~/Documents-private' is NOT treated as being inside '~/Documents'.
    """
    try:
        p.resolve().relative_to(ROOT_DIR)
        return True
    except ValueError:
        return False


def _resolve_safe(rel_path: str) -> Path:
    """Resolve a relative path inside ROOT_DIR; raise if it escapes (.., absolute paths, symlinks)."""
    resolved = (ROOT_DIR / rel_path).resolve()
    if not _inside_root(resolved):
        raise ValueError(f"Path '{rel_path}' escapes the allowed root '{ROOT_DIR}'.")
    return resolved


def _file_meta(p: Path) -> dict:
    stat = p.stat()
    return {
        "name": p.name,
        "path": str(p.resolve().relative_to(ROOT_DIR)),
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "type": "directory" if p.is_dir() else "file",
        "extension": p.suffix.lower() if p.is_file() else "",
    }


def _norm_exts(exts: Optional[list[str]]) -> Optional[set[str]]:
    if not exts:
        return None
    return {e.lower() if e.startswith(".") else f".{e.lower()}" for e in exts}


def _fmt_error(e: Exception) -> str:
    return json.dumps({"error": str(e), "type": type(e).__name__})


# ── Input Models ──────────────────────────────────────────────────────────────

class PathInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path: str = Field(..., description="Relative path from the configured root directory (e.g. 'Reports/Q1.txt')", min_length=1)


class WriteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path: str = Field(..., description="Relative destination path (e.g. 'Notes/idea.txt')", min_length=1)
    content: str = Field(..., description="Text content to write to the file")
    overwrite: bool = Field(default=False, description="Set to true to overwrite an existing file")


class ListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path: str = Field(default=".", description="Relative directory path to list (default: root)")
    extensions: Optional[list[str]] = Field(default=None, description="Filter by extensions, e.g. ['.txt', '.md']")
    recursive: bool = Field(default=False, description="List recursively into subdirectories")


class SearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., description="Text to search for inside file contents", min_length=1)
    path: str = Field(default=".", description="Relative directory to search in")
    extensions: Optional[list[str]] = Field(default=None, description="Limit search to these file extensions")
    max_results: int = Field(default=20, ge=1, le=100, description="Maximum number of matching files to return")


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool(name="file_read", annotations=READ_ONLY)
async def file_read(params: PathInput) -> str:
    """Read the full text content of a file (up to 5 MB by default).

    Args:
        params.path: Relative path to the file from the root directory.

    Returns:
        str: Plain text content of the file, or a JSON error object.
    """
    try:
        target = _resolve_safe(params.path)
        if not target.is_file():
            return _fmt_error(FileNotFoundError(f"'{params.path}' is not a file."))
        if target.stat().st_size > MAX_READ_BYTES:
            return _fmt_error(ValueError(f"'{params.path}' is larger than {MAX_READ_BYTES} bytes."))
        return target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(
    name="file_write",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
)
async def file_write(params: WriteInput) -> str:
    """Write text content to a file, creating parent directories as needed.

    Args:
        params.path: Relative destination path.
        params.content: Text to write.
        params.overwrite: Must be true to overwrite an existing file.

    Returns:
        str: JSON with success status and file metadata.
    """
    try:
        target = _resolve_safe(params.path)
        if target == ROOT_DIR or target.is_dir():
            return _fmt_error(IsADirectoryError(f"'{params.path}' is a directory."))
        if target.exists() and not params.overwrite:
            return _fmt_error(FileExistsError(f"'{params.path}' already exists. Set overwrite=true to replace it."))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(params.content, encoding="utf-8")
        return json.dumps({"success": True, "file": _file_meta(target)}, indent=2)
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(name="file_list", annotations=READ_ONLY)
async def file_list(params: ListInput) -> str:
    """List files and directories at a given path (max 2000 entries).

    Args:
        params.path: Relative directory path (default: root).
        params.extensions: Optional extension filter (e.g. ['.md', '.txt']).
        params.recursive: Whether to recurse into subdirectories.

    Returns:
        str: JSON list of file metadata objects.
    """
    try:
        target = _resolve_safe(params.path)
        if not target.is_dir():
            return _fmt_error(NotADirectoryError(f"'{params.path}' is not a directory."))

        pattern = "**/*" if params.recursive else "*"
        exts = _norm_exts(params.extensions)
        entries = []
        truncated = False
        for e in target.glob(pattern):
            if not _inside_root(e):  # skip symlinks that point outside the root
                continue
            if exts and e.is_file() and e.suffix.lower() not in exts:
                continue
            entries.append(e)
            if len(entries) >= MAX_LIST_ENTRIES:
                truncated = True
                break
        entries.sort(key=lambda p: (p.is_file(), p.name.lower()))

        return json.dumps({
            "root": str(ROOT_DIR),
            "path": params.path,
            "count": len(entries),
            "truncated": truncated,
            "entries": [_file_meta(e) for e in entries],
        }, indent=2)
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(name="file_search", annotations=READ_ONLY)
async def file_search(params: SearchInput) -> str:
    """Search for text inside files within a directory (case-insensitive).

    Files larger than the read limit and symlinks pointing outside the root are skipped.

    Args:
        params.query: The text string to search for.
        params.path: Directory to search in.
        params.extensions: Optional file extension filter.
        params.max_results: Maximum number of matching files.

    Returns:
        str: JSON list of files containing the query, with matching line previews.
    """
    try:
        target = _resolve_safe(params.path)
        exts = _norm_exts(params.extensions)
        matches = []
        query_lower = params.query.lower()

        for f in sorted(target.rglob("*")):
            if len(matches) >= params.max_results:
                break
            if not f.is_file() or not _inside_root(f):
                continue
            if exts and f.suffix.lower() not in exts:
                continue
            try:
                if f.stat().st_size > MAX_READ_BYTES:
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            lines_found = [
                {"line": i + 1, "text": line.strip()[:300]}
                for i, line in enumerate(text.splitlines())
                if query_lower in line.lower()
            ]
            if lines_found:
                matches.append({**_file_meta(f), "matches": lines_found[:5]})

        return json.dumps({"query": params.query, "total_files_matched": len(matches), "results": matches}, indent=2)
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(
    name="file_delete",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
)
async def file_delete(params: PathInput) -> str:
    """Delete a single file (never a directory). Irreversible - use with care.

    Args:
        params.path: Relative path of the file to delete.

    Returns:
        str: JSON confirmation or error.
    """
    try:
        target = _resolve_safe(params.path)
        if not target.is_file():
            return _fmt_error(FileNotFoundError(f"'{params.path}' is not a file or does not exist."))
        meta = _file_meta(target)
        target.unlink()
        return json.dumps({"success": True, "deleted": meta}, indent=2)
    except Exception as e:
        return _fmt_error(e)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Banner goes to stderr - stdout is reserved for the MCP JSON-RPC protocol.
    print(f"[file_manager_mcp] Root directory: {ROOT_DIR}", file=sys.stderr)
    mcp.run()
