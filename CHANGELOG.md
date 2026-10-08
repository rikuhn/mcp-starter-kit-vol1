# Changelog

## 1.1.0 - 2026-10 (first open-source release)

- Works with MCP Python SDK 1.x **and** 2.x (`FastMCP` was renamed to `MCPServer` in 2.0; v1-only code fails to start on fresh installs).
- Installer uses its own virtual environment (`.venv/`) instead of installing into the system Python; dependencies pinned in `requirements.txt`.
- file-manager: root check uses real path containment (a sibling folder with the same prefix was reachable before); symlinks pointing outside the root are skipped; size limits for read/search/list.
- web-scraper: every redirect hop is re-validated (redirects to internal addresses were followed before); IPv4-mapped IPv6 handled; 5 MB response cap.
- sqlite: reads never create a missing database; table names quoted safely; `fetchmany` instead of loading whole tables.
- pdf-export: file names confined to `PDF_OUTPUT_DIR`, no overwrite, title/author HTML-escaped, WeasyPrint blocked from loading local files and network resources, no Google Fonts download.
- notes: atomic writes, file mode 0600, a corrupt file is never overwritten.
- New `tests/smoke_test.py` and CI on macOS, Windows and Linux.

## 1.0.0 - 2026-06

- Initial release (sold on Gumroad).
