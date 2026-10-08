"""
SQLite MCP Server
=================
Query, explore and (optionally) write SQLite databases in plain language via Claude.
Read-only by default: reads use a `mode=ro` connection, so even a crafted
SELECT cannot modify the database.

Part of MCP Starter Kit Vol. 1 - https://github.com/rikuhn/mcp-starter-kit-vol1
License: MIT
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
from mcp.types import ToolAnnotations

# ── Server ────────────────────────────────────────────────────────────────────
mcp = _Server("sqlite_mcp")

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH = Path(os.environ.get("SQLITE_DB_PATH", str(Path.home() / "data.db"))).expanduser()
READ_ONLY_MODE = os.environ.get("SQLITE_READ_ONLY", "true").strip().lower() in ("1", "true", "yes")
MAX_ROWS = max(1, int(os.environ.get("SQLITE_MAX_ROWS", "500")))

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_connection(write: bool = False) -> sqlite3.Connection:
    """Open the database. Reads always use mode=ro; writes need SQLITE_READ_ONLY=false."""
    if write and READ_ONLY_MODE:
        raise PermissionError("Server is running in read-only mode. Set SQLITE_READ_ONLY=false to enable writes.")
    if not write and not DB_PATH.exists():
        # Do not silently create an empty database on a read.
        raise FileNotFoundError(f"Database not found: {DB_PATH}")
    mode = "rwc" if write else "ro"
    # Path.as_uri() percent-encodes '?', '#' and spaces, so odd file names cannot inject URI parameters.
    uri = f"{DB_PATH.resolve().as_uri()}?mode={mode}"
    conn = sqlite3.connect(uri, uri=True, timeout=10, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    return conn


def _quote_ident(name: str) -> str:
    """Quote an SQL identifier safely ("" escaping)."""
    return '"' + name.replace('"', '""') + '"'


def _rows_to_dicts(cursor: sqlite3.Cursor, limit: int) -> list[dict]:
    cols = [d[0] for d in cursor.description] if cursor.description else []
    return [dict(zip(cols, row)) for row in cursor.fetchmany(limit)]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?", (table,))
    return cur.fetchone() is not None


def _fmt_error(e: Exception) -> str:
    return json.dumps({"error": str(e), "type": type(e).__name__})


# ── Input Models ──────────────────────────────────────────────────────────────

class QueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sql: str = Field(..., description="SQL SELECT statement to execute (e.g. 'SELECT * FROM users LIMIT 10')", min_length=5)
    limit: int = Field(default=100, ge=1, le=MAX_ROWS, description=f"Maximum rows to return (max {MAX_ROWS})")

    @field_validator("sql")
    @classmethod
    def only_select(cls, v: str) -> str:
        first_word = v.strip().split()[0].upper()
        if first_word not in ("SELECT", "WITH", "EXPLAIN"):
            raise ValueError("Only SELECT / WITH / EXPLAIN statements are allowed in query mode. Use sqlite_execute for writes.")
        return v


class ExecuteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sql: str = Field(..., description="SQL statement to execute (INSERT, UPDATE, DELETE, CREATE, DROP, etc.)", min_length=5)
    confirm: bool = Field(..., description="Must be set to true to confirm intentional write operation")

    @field_validator("confirm")
    @classmethod
    def must_confirm(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Set confirm=true to acknowledge this is a write operation.")
        return v


class TableInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    table_name: str = Field(..., description="Name of the table to inspect", min_length=1, max_length=128)


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool(name="sqlite_query", annotations=READ_ONLY)
async def sqlite_query(params: QueryInput) -> str:
    """Run a SELECT query against the SQLite database and return results as JSON.

    Args:
        params.sql: A single SELECT (or WITH/EXPLAIN) statement.
        params.limit: Maximum rows to return.

    Returns:
        str: JSON object with 'columns', 'rows' and 'count'.
    """
    try:
        conn = _get_connection(write=False)
        try:
            cursor = conn.execute(params.sql)
            rows = _rows_to_dicts(cursor, params.limit)
        finally:
            conn.close()
        return json.dumps({"count": len(rows), "columns": list(rows[0].keys()) if rows else [], "rows": rows}, indent=2, default=str)
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(
    name="sqlite_execute",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
)
async def sqlite_execute(params: ExecuteInput) -> str:
    """Execute a single write SQL statement (INSERT, UPDATE, DELETE, CREATE, DROP).

    Requires SQLITE_READ_ONLY=false in the server environment and confirm=true.

    Args:
        params.sql: The SQL statement to execute.
        params.confirm: Must be true.

    Returns:
        str: JSON with rows_affected and last_insert_id.
    """
    try:
        conn = _get_connection(write=True)
        try:
            cursor = conn.execute(params.sql)
            conn.commit()
            result = {"success": True, "rows_affected": cursor.rowcount, "last_insert_id": cursor.lastrowid}
        finally:
            conn.close()
        return json.dumps(result, indent=2)
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(name="sqlite_schema", annotations=READ_ONLY)
async def sqlite_schema() -> str:
    """List all tables in the database with their column definitions.

    Returns:
        str: JSON object mapping table names to their column schemas.
    """
    try:
        conn = _get_connection(write=False)
        try:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            schema = {}
            for table in tables:
                cols = conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
                schema[table] = [
                    {"cid": r[0], "name": r[1], "type": r[2], "notnull": bool(r[3]), "default": r[4], "pk": bool(r[5])}
                    for r in cols
                ]
        finally:
            conn.close()
        return json.dumps({"database": str(DB_PATH), "table_count": len(tables), "tables": schema}, indent=2)
    except Exception as e:
        return _fmt_error(e)


@mcp.tool(name="sqlite_sample", annotations=READ_ONLY)
async def sqlite_sample(params: TableInput) -> str:
    """Return the first 10 rows of a table plus its total row count.

    Args:
        params.table_name: The table to sample.

    Returns:
        str: JSON with total row count and first 10 rows.
    """
    try:
        conn = _get_connection(write=False)
        try:
            if not _table_exists(conn, params.table_name):
                return _fmt_error(KeyError(f"Table '{params.table_name}' not found."))
            ident = _quote_ident(params.table_name)
            total = conn.execute(f"SELECT COUNT(*) FROM {ident}").fetchone()[0]
            rows = _rows_to_dicts(conn.execute(f"SELECT * FROM {ident} LIMIT 10"), 10)
        finally:
            conn.close()
        return json.dumps({"table": params.table_name, "total_rows": total, "sample": rows}, indent=2, default=str)
    except Exception as e:
        return _fmt_error(e)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mode_label = "READ-ONLY" if READ_ONLY_MODE else "READ-WRITE"
    print(f"[sqlite_mcp] DB: {DB_PATH} | Mode: {mode_label}", file=sys.stderr)
    mcp.run()
