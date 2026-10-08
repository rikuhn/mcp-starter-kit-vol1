# MCP Starter Kit Vol. 1

**Five small, readable MCP servers that give Claude safe access to your files, the web, SQLite, PDF export and persistent notes, installed with one script.**

[![smoke-test](https://github.com/rikuhn/mcp-starter-kit-vol1/actions/workflows/smoke.yml/badge.svg)](https://github.com/rikuhn/mcp-starter-kit-vol1/actions/workflows/smoke.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP SDK 1.x and 2.x](https://img.shields.io/badge/MCP%20SDK-1.x%20%7C%202.x-6c5ce7)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

## Why this kit

- **One installer, five servers.** No Node, no Docker, no manual JSON editing. The installer creates its own virtual environment and merges the servers into your Claude Desktop config without touching your other servers.
- **Safe by default.** File access is locked to one folder, SQLite opens read-only, the web fetcher refuses internal addresses, PDF export cannot read local files. Every one of these rules is covered by the smoke test.
- **Built to be read.** Each server is a single Python file of about 200 to 300 lines. If you want to write your own MCP server, copy one and change it.
- **Works with MCP Python SDK 1.x and 2.x**, on macOS, Windows and Linux.

## What's inside

| Server | What it does | Tools |
|---|---|---|
| **file-manager** | Read, write, list, search and delete files inside one root folder | `file_read`, `file_write`, `file_list`, `file_search`, `file_delete` |
| **web-scraper** | Fetch public web pages as clean Markdown | `web_fetch`, `web_fetch_multi` |
| **sqlite** | Explore and query a SQLite database; writes only when you enable them | `sqlite_query`, `sqlite_execute`, `sqlite_schema`, `sqlite_sample` |
| **pdf-export** | Turn Claude's Markdown answer into a styled PDF | `pdf_export`, `pdf_check_deps` |
| **notes** | A notepad that survives between Claude sessions | `note_create`, `note_get`, `note_update`, `note_delete`, `note_search` |

## Requirements

- [Claude Desktop](https://claude.ai/download) or [Claude Code](https://docs.claude.com/en/docs/claude-code) (any MCP client that speaks stdio works)
- Python 3.10 or newer. On macOS the installer offers to install it for you.
- Optional, for real PDFs: WeasyPrint's native libraries (`brew install pango` on macOS). Without them, PDF export saves an HTML file.

## Install

```bash
git clone https://github.com/rikuhn/mcp-starter-kit-vol1.git
cd mcp-starter-kit-vol1
```

**macOS / Linux**

```bash
./scripts/install.sh
```

**Windows (PowerShell)**

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install.ps1
```

Then quit Claude Desktop completely and reopen it. The five servers appear under the tools icon.

The installer:

1. finds Python 3.10+,
2. creates `.venv/` inside the kit folder and installs `requirements.txt` there (your system Python stays untouched),
3. checks every server file,
4. backs up `claude_desktop_config.json` to `.bak` and merges the five servers into it.

### Claude Code

Run the installer once (it creates `.venv/`), then register the servers you want:

```bash
KIT="$(pwd)"
claude mcp add --scope user --env FILE_MANAGER_ROOT="$HOME/Documents" file-manager -- "$KIT/.venv/bin/python" "$KIT/servers/file_manager_mcp.py"
claude mcp add --scope user web-scraper -- "$KIT/.venv/bin/python" "$KIT/servers/web_scraper_mcp.py"
claude mcp add --scope user --env SQLITE_DB_PATH="$HOME/data.db" sqlite -- "$KIT/.venv/bin/python" "$KIT/servers/sqlite_mcp.py"
claude mcp add --scope user --env PDF_OUTPUT_DIR="$HOME/Desktop" pdf-export -- "$KIT/.venv/bin/python" "$KIT/servers/pdf_export_mcp.py"
claude mcp add --scope user notes -- "$KIT/.venv/bin/python" "$KIT/servers/notes_mcp.py"
```

### Manual setup

Copy the blocks you need from [`config/claude_desktop_config.template.json`](config/claude_desktop_config.template.json) into your Claude Desktop config and replace the placeholder paths:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

## Try it

```
Read Reports/Q2.txt and summarise it in five bullet points.
Fetch https://news.ycombinator.com and list the top 10 stories.
Show me the schema of my database, then count users who signed up last month.
Export your last answer as a PDF called "Weekly report".
Save a note: follow up with the client on Friday, tag it "work".
```

## Configuration

Set these in the `env` block of each server.

| Server | Variable | Default | Meaning |
|---|---|---|---|
| file-manager | `FILE_MANAGER_ROOT` | `~/Documents` | The only folder the server can touch |
| file-manager | `FILE_MANAGER_MAX_READ_BYTES` | `5242880` | Files larger than this are not read or searched |
| sqlite | `SQLITE_DB_PATH` | `~/data.db` | Database file |
| sqlite | `SQLITE_READ_ONLY` | `true` | Set `false` to allow INSERT/UPDATE/DELETE/CREATE |
| sqlite | `SQLITE_MAX_ROWS` | `500` | Upper limit for rows per query |
| pdf-export | `PDF_OUTPUT_DIR` | `~/Desktop` | Where PDFs are written |
| notes | `NOTES_FILE` | `~/.mcp_notes.json` | Where notes are stored (created with mode 0600) |

## Security model

| Server | Guard rails |
|---|---|
| file-manager | Every path is resolved (including symlinks) and must stay inside `FILE_MANAGER_ROOT`. `..`, absolute paths, sibling folders with the same prefix and symlinks pointing outside are refused. No overwrite without `overwrite=true`. Delete removes single files only. |
| web-scraper | http/https only. Hosts that resolve to loopback, private, link-local (cloud metadata), reserved or multicast addresses are refused; every redirect hop is checked again. Responses are capped at 5 MB. |
| sqlite | Reads use a `mode=ro` connection, so even a crafted `WITH ... DELETE` cannot write. Writes need `SQLITE_READ_ONLY=false` **and** `confirm=true`. Table names are quoted, one statement per call. |
| pdf-export | File names are reduced to one safe component inside `PDF_OUTPUT_DIR`; existing files are never overwritten. WeasyPrint may only load inline `data:` resources, so a document cannot read local files or call out to the network. |
| notes | Atomic writes, owner-only file permissions. A corrupt notes file raises an error instead of being silently replaced. |

Known limit: the SSRF check resolves DNS before the request. A hostile DNS server could still answer differently a moment later (DNS rebinding). Do not run the web-scraper on a machine where internal services trust anything that comes from localhost. See [SECURITY.md](SECURITY.md).

Remember that an MCP server acts with **your** permissions. Claude asks before it calls a tool; read what it wants to do before you approve.

## Test

```bash
.venv/bin/python tests/smoke_test.py            # offline, about 40 checks
SMOKE_ONLINE=1 .venv/bin/python tests/smoke_test.py   # adds one real web fetch
```

The test starts each server with a real MCP client over stdio and calls every tool, including the refusal cases. CI runs it on macOS, Windows and Linux with MCP SDK 1.x and 2.x.

## Uninstall

```bash
./scripts/uninstall.sh       # macOS / Linux
.\scripts\uninstall.ps1      # Windows
```

It removes the five entries from the Claude config (backup first, other servers kept) and asks before deleting your notes file or the kit folder.

## Pro bundle

Vol. 1 is free and stays free. If it is useful to you, **Vol. 2 "Local Power Tools"** adds four more servers for the local, private work no cloud connector covers:

| Server | What it does |
|---|---|
| **Doc Search** | Offline full-text search across your documents, including the text inside PDFs |
| **Obsidian Vault** | Search, read, create and link notes in an Obsidian vault, with backlinks and tags |
| **Git Companion** | Status, diff, log and blame for a local repo; drafts commit messages from the real diff |
| **Disk Insights** | Read-only view of what is eating your disk space |

Same installer style, same safety approach. Buying the bundle (Vol. 1 + Vol. 2) also supports maintenance of this free kit.

**[Get the MCP Starter Kit Bundle on Gumroad](https://kuhnrichard.gumroad.com/l/mcp-bundle)**

## Contributing

Issues and pull requests are welcome. Please run `tests/smoke_test.py` before you open a PR, and add a check for any new guard rail.

## License

[MIT](LICENSE) © 2026 Richard Kuhn. Dependencies: `mcp` (MIT), `httpx` (BSD-3-Clause), `pydantic` (MIT), `markdownify` (MIT), and optionally `weasyprint` (BSD-3-Clause) and `markdown` (BSD-3-Clause).
