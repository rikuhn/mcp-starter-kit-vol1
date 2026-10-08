"""
Quick Notes MCP Server
======================
A lightweight persistent notepad inside Claude - save, retrieve, tag
and search notes across sessions. Zero external dependencies beyond
the MCP SDK.

Part of MCP Starter Kit Vol. 1 - https://github.com/rikuhn/mcp-starter-kit-vol1
License: MIT
"""

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
from mcp.types import ToolAnnotations

# ── Server ────────────────────────────────────────────────────────────────────
mcp = _Server("notes_mcp")

NOTES_FILE = Path(os.environ.get("NOTES_FILE", str(Path.home() / ".mcp_notes.json"))).expanduser()


# ── Storage helpers ───────────────────────────────────────────────────────────

def _load() -> dict:
    """Load all notes. A corrupt file raises instead of returning {} - otherwise the
    next save would silently wipe every existing note."""
    if not NOTES_FILE.exists():
        return {}
    try:
        data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Notes file {NOTES_FILE} is not valid JSON ({e}). Fix or move it; nothing was changed.")
    if not isinstance(data, dict):
        raise ValueError(f"Notes file {NOTES_FILE} has an unexpected format; nothing was changed.")
    return data


def _save(data: dict) -> None:
    """Atomic write (temp file + rename) with owner-only permissions."""
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".notes-", suffix=".json", dir=str(NOTES_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, NOTES_FILE)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _fmt_error(e: Exception) -> str:
    return json.dumps({"error": str(e), "type": type(e).__name__})


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Input Models ──────────────────────────────────────────────────────────────

class CreateNoteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(..., description="Short title for the note (e.g. 'Meeting recap 2026-06-25')", min_length=1, max_length=200)
    content: str = Field(..., description="Note content (Markdown supported)", min_length=1)
    tags: list[str] = Field(default_factory=list, description="Optional tags for filtering (e.g. ['project', 'idea'])", max_length=10)


class NoteIdInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    note_id: str = Field(..., description="The unique ID of the note to retrieve/delete", min_length=1)


class UpdateNoteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    note_id: str = Field(..., description="The unique ID of the note to update", min_length=1)
    title: Optional[str] = Field(default=None, description="New title (leave empty to keep existing)")
    content: Optional[str] = Field(default=None, description="New content (leave empty to keep existing)")
    tags: Optional[list[str]] = Field(default=None, description="New tags (leave empty to keep existing)")


class SearchNotesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: Optional[str] = Field(default=None, description="Text to search in title and content (case-insensitive)")
    tag: Optional[str] = Field(default=None, description="Filter by a specific tag")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum number of notes to return")


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="note_create",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
async def note_create(params: CreateNoteInput) -> str:
    """Create a new note and save it persistently.

    Args:
        params.title: Short note title.
        params.content: Note body (Markdown supported).
        params.tags: Optional list of tags.

    Returns:
        str: JSON with the created note including its ID.
    """
    try:
        notes = _load()
        note_id = str(uuid4())[:8]
        note = {
            "id": note_id,
            "title": params.title,
            "content": params.content,
            "tags": params.tags,
            "created": _now(),
            "updated": _now(),
        }
        notes[note_id] = note
        _save(notes)
        return json.dumps({"success": True, "note": note}, indent=2, ensure_ascii=False)
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(
    name="note_get",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def note_get(params: NoteIdInput) -> str:
    """Retrieve a single note by its ID.

    Args:
        params.note_id: The ID returned when the note was created.

    Returns:
        str: JSON note object or error if not found.
    """
    try:
        notes = _load()
        if params.note_id not in notes:
            return _fmt_error(KeyError(f"Note '{params.note_id}' not found."))
        return json.dumps(notes[params.note_id], indent=2, ensure_ascii=False)
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(
    name="note_update",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def note_update(params: UpdateNoteInput) -> str:
    """Update an existing note's title, content or tags.

    Args:
        params.note_id: ID of the note to update.
        params.title: New title (optional).
        params.content: New content (optional).
        params.tags: New tags (optional).

    Returns:
        str: JSON with updated note.
    """
    try:
        notes = _load()
        if params.note_id not in notes:
            return _fmt_error(KeyError(f"Note '{params.note_id}' not found."))
        note = notes[params.note_id]
        if params.title is not None:
            note["title"] = params.title
        if params.content is not None:
            note["content"] = params.content
        if params.tags is not None:
            note["tags"] = params.tags
        note["updated"] = _now()
        _save(notes)
        return json.dumps({"success": True, "note": note}, indent=2, ensure_ascii=False)
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(
    name="note_delete",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
)
async def note_delete(params: NoteIdInput) -> str:
    """Permanently delete a note by its ID.

    Args:
        params.note_id: ID of the note to delete.

    Returns:
        str: JSON confirmation.
    """
    try:
        notes = _load()
        if params.note_id not in notes:
            return _fmt_error(KeyError(f"Note '{params.note_id}' not found."))
        deleted = notes.pop(params.note_id)
        _save(notes)
        return json.dumps({"success": True, "deleted_note": deleted}, indent=2, ensure_ascii=False)
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(
    name="note_search",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def note_search(params: SearchNotesInput) -> str:
    """Search and list notes, optionally filtered by query text or tag.

    Args:
        params.query: Text to search in title and content (optional).
        params.tag: Filter by this specific tag (optional).
        params.limit: Maximum results.

    Returns:
        str: JSON list of matching notes (content truncated to 300 chars for overview).
    """
    try:
        notes = _load()
        results = list(notes.values())

        if params.tag:
            results = [n for n in results if params.tag.lower() in [t.lower() for t in n.get("tags", [])]]

        if params.query:
            q = params.query.lower()
            results = [n for n in results if q in n["title"].lower() or q in n["content"].lower()]

        results = sorted(results, key=lambda n: n["updated"], reverse=True)[: params.limit]

        # Truncate content for list view
        preview = [{**n, "content": n["content"][:300] + ("..." if len(n["content"]) > 300 else "")} for n in results]

        return json.dumps({
            "total_notes": len(notes),
            "matched": len(preview),
            "notes": preview,
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return _fmt_error(e)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Banner goes to stderr - stdout is reserved for the MCP JSON-RPC protocol.
    print(f"[notes_mcp] Notes file: {NOTES_FILE}", file=sys.stderr)
    try:
        print(f"  {len(_load())} existing note(s) loaded.", file=sys.stderr)
    except ValueError as e:  # keep the server usable for reads of the error message
        print(f"  WARNING: {e}", file=sys.stderr)
    mcp.run()
