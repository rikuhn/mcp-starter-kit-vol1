"""
Smoke test for MCP Starter Kit Vol. 1
=====================================
Starts every server over stdio with a real MCP client, lists its tools and
calls each one at least once - including the safety cases (path escape,
SSRF, read-only SQL, file-name traversal). Uses only temporary folders.

Run:  python tests/smoke_test.py          (exit code 0 = all checks passed)
Works with MCP Python SDK 1.x and 2.x.
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
SERVERS = ROOT / "servers"
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + label)
    if not cond:
        FAILURES.append(label)


def text_of(result) -> str:
    """Concatenate the text content of a CallToolResult."""
    parts = []
    for c in getattr(result, "content", []) or []:
        if getattr(c, "type", "") == "text":
            parts.append(c.text)
    return "\n".join(parts)


def is_error(result) -> bool:
    """A refusal is either a JSON {"error": ...} payload or an MCP tool error."""
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        return True
    try:
        return "error" in json.loads(text_of(result))
    except (json.JSONDecodeError, TypeError):
        return False


async def run_server(script: str, env: dict, scenario) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVERS / script)],
        env={**os.environ, **env},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"\n[{script}] tools: {', '.join(names)}")
            await scenario(session, names)


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="mcp-kit-smoke-"))
    root = tmp / "root"
    root.mkdir()
    (tmp / "root-sibling").mkdir()
    (tmp / "root-sibling" / "secret.txt").write_text("must not be readable")
    (tmp / "outside.txt").write_text("outside")
    (root / "hello.md").write_text("# Hello\nneedle in a haystack\n")
    try:
        os.symlink(tmp / "outside.txt", root / "link-out.txt")
    except OSError:
        pass

    # ── File Manager ─────────────────────────────────────────────────────────
    async def fm(s, names):
        check(names == sorted(["file_read", "file_write", "file_list", "file_search", "file_delete"]), "file_manager exposes 5 tools")
        r = await s.call_tool("file_read", {"params": {"path": "hello.md"}})
        check("needle" in text_of(r), "file_read reads a file inside root")
        r = await s.call_tool("file_read", {"params": {"path": "../outside.txt"}})
        check(is_error(r), "file_read blocks ../ escape")
        r = await s.call_tool("file_read", {"params": {"path": "../root-sibling/secret.txt"}})
        check(is_error(r), "file_read blocks sibling-prefix escape (root vs root-sibling)")
        r = await s.call_tool("file_read", {"params": {"path": str(tmp / "outside.txt")}})
        check(is_error(r), "file_read blocks absolute path")
        if (root / "link-out.txt").is_symlink():
            r = await s.call_tool("file_read", {"params": {"path": "link-out.txt"}})
            check(is_error(r), "file_read blocks symlink pointing outside root")
        r = await s.call_tool("file_write", {"params": {"path": "sub/new.txt", "content": "hi"}})
        check(not is_error(r) and (root / "sub" / "new.txt").read_text() == "hi", "file_write creates file")
        r = await s.call_tool("file_write", {"params": {"path": "sub/new.txt", "content": "again"}})
        check(is_error(r), "file_write refuses overwrite without flag")
        r = await s.call_tool("file_list", {"params": {"path": ".", "recursive": True}})
        listed = json.loads(text_of(r))
        check(listed["count"] >= 2 and all("link-out" not in e["name"] for e in listed["entries"]), "file_list lists root, hides outside symlink")
        r = await s.call_tool("file_search", {"params": {"query": "needle"}})
        check(json.loads(text_of(r))["total_files_matched"] == 1, "file_search finds text")
        r = await s.call_tool("file_delete", {"params": {"path": "sub/new.txt"}})
        check(not (root / "sub" / "new.txt").exists(), "file_delete removes file")

    await run_server("file_manager_mcp.py", {"FILE_MANAGER_ROOT": str(root)}, fm)

    # ── Web Scraper (offline-safe: only refusal paths are tested) ────────────
    async def ws(s, names):
        check(names == ["web_fetch", "web_fetch_multi"], "web_scraper exposes 2 tools")
        for url in ["http://127.0.0.1:8080/", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
                    "http://[::ffff:127.0.0.1]/", "file:///etc/passwd", "http://10.0.0.1/admin"]:
            r = await s.call_tool("web_fetch", {"params": {"url": url}})
            check(is_error(r), f"web_fetch refuses {url}")
        r = await s.call_tool("web_fetch_multi", {"params": {"urls": ["http://127.0.0.1/"]}})
        check(is_error(r) or "error" in text_of(r), "web_fetch_multi refuses internal URL")
        if os.environ.get("SMOKE_ONLINE") == "1":
            r = await s.call_tool("web_fetch", {"params": {"url": "https://example.com/"}})
            check("Example Domain" in text_of(r), "web_fetch reads a public page (online)")

    await run_server("web_scraper_mcp.py", {}, ws)

    # ── SQLite ───────────────────────────────────────────────────────────────
    db = tmp / "test.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO users (name) VALUES (?)", [("ada",), ("linus",), ("grace",)])
    con.execute('CREATE TABLE "odd""name" (x INTEGER)')
    con.commit()
    con.close()

    async def sq_ro(s, names):
        check(names == sorted(["sqlite_query", "sqlite_execute", "sqlite_schema", "sqlite_sample"]), "sqlite exposes 4 tools")
        r = await s.call_tool("sqlite_query", {"params": {"sql": "SELECT name FROM users ORDER BY id", "limit": 2}})
        check(json.loads(text_of(r))["count"] == 2, "sqlite_query respects limit")
        r = await s.call_tool("sqlite_query", {"params": {"sql": "DELETE FROM users"}})
        check(is_error(r), "sqlite_query rejects DELETE")
        r = await s.call_tool("sqlite_query", {"params": {"sql": "WITH x AS (SELECT 1) DELETE FROM users"}})
        check(is_error(r), "sqlite_query cannot write via CTE (mode=ro)")
        r = await s.call_tool("sqlite_execute", {"params": {"sql": "DELETE FROM users", "confirm": True}})
        check(is_error(r), "sqlite_execute blocked in read-only mode")
        r = await s.call_tool("sqlite_schema", {})
        check("users" in json.loads(text_of(r))["tables"], "sqlite_schema lists tables")
        r = await s.call_tool("sqlite_sample", {"params": {"table_name": 'odd"name'}})
        check(not is_error(r), "sqlite_sample handles quoted table names")
        r = await s.call_tool("sqlite_sample", {"params": {"table_name": "users' UNION SELECT 1 --"}})
        check(is_error(r), "sqlite_sample rejects injected table name")

    await run_server("sqlite_mcp.py", {"SQLITE_DB_PATH": str(db), "SQLITE_READ_ONLY": "true"}, sq_ro)

    async def sq_rw(s, names):
        r = await s.call_tool("sqlite_execute", {"params": {"sql": "INSERT INTO users (name) VALUES ('new')", "confirm": True}})
        check(json.loads(text_of(r)).get("rows_affected") == 1, "sqlite_execute writes when enabled")

    await run_server("sqlite_mcp.py", {"SQLITE_DB_PATH": str(db), "SQLITE_READ_ONLY": "false"}, sq_rw)

    missing = tmp / "does-not-exist.db"

    async def sq_missing(s, names):
        r = await s.call_tool("sqlite_schema", {})
        check(is_error(r) and not missing.exists(), "read on missing DB errors and does not create a file")

    await run_server("sqlite_mcp.py", {"SQLITE_DB_PATH": str(missing)}, sq_missing)

    # ── PDF Export ───────────────────────────────────────────────────────────
    out = tmp / "pdf-out"

    async def pdf(s, names):
        check(names == ["pdf_check_deps", "pdf_export"], "pdf_export exposes 2 tools")
        r = await s.call_tool("pdf_check_deps", {})
        deps = json.loads(text_of(r))
        print(f"    (weasyprint available: {deps['weasyprint']})")
        r = await s.call_tool("pdf_export", {"params": {"content": "# Hi\n\nText", "filename": "../../escape", "title": "<b>T</b>"}})
        res = json.loads(text_of(r))
        written = Path(res.get("path", ""))
        check(res.get("success") and written.parent == out, "pdf_export keeps output inside PDF_OUTPUT_DIR")
        r = await s.call_tool("pdf_export", {"params": {"content": "x", "filename": "escape"}})
        check(json.loads(text_of(r)).get("path") != str(written), "pdf_export never overwrites")
        if written.suffix == ".html":
            check("<b>T</b>" not in written.read_text(), "title is HTML-escaped")

    await run_server("pdf_export_mcp.py", {"PDF_OUTPUT_DIR": str(out)}, pdf)

    # ── Notes ────────────────────────────────────────────────────────────────
    notes_file = tmp / "notes.json"

    async def notes(s, names):
        check(len(names) == 5, "notes exposes 5 tools")
        r = await s.call_tool("note_create", {"params": {"title": "Smoke", "content": "remember the milk", "tags": ["t"]}})
        nid = json.loads(text_of(r))["note"]["id"]
        r = await s.call_tool("note_search", {"params": {"query": "milk"}})
        check(json.loads(text_of(r))["matched"] == 1, "note_search finds note")
        r = await s.call_tool("note_update", {"params": {"note_id": nid, "title": "Smoke 2"}})
        check(json.loads(text_of(r))["note"]["title"] == "Smoke 2", "note_update works")
        r = await s.call_tool("note_get", {"params": {"note_id": nid}})
        check(json.loads(text_of(r))["id"] == nid, "note_get works")
        r = await s.call_tool("note_delete", {"params": {"note_id": nid}})
        check(json.loads(text_of(r))["success"], "note_delete works")
        if os.name == "posix":
            check(oct(notes_file.stat().st_mode & 0o777) == "0o600", "notes file is owner-only (0600)")

    await run_server("notes_mcp.py", {"NOTES_FILE": str(notes_file)}, notes)

    corrupt = tmp / "corrupt.json"
    corrupt.write_text("{not json")

    async def notes_corrupt(s, names):
        r = await s.call_tool("note_create", {"params": {"title": "x", "content": "y"}})
        check(is_error(r) and corrupt.read_text() == "{not json", "corrupt notes file is never overwritten")

    await run_server("notes_mcp.py", {"NOTES_FILE": str(corrupt)}, notes_corrupt)

    import importlib.metadata as md
    print(f"\nmcp SDK {md.version('mcp')} · Python {sys.version.split()[0]}")
    print(f"{'ALL CHECKS PASSED' if not FAILURES else f'{len(FAILURES)} CHECK(S) FAILED'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
