#!/usr/bin/env bash
# ============================================================
#  MCP Starter Kit Vol. 1 - Installer (macOS / Linux)
#  https://github.com/rikuhn/mcp-starter-kit-vol1
#
#  What it does:
#   1. finds Python 3.10+ (offers to install it on macOS)
#   2. creates a private virtual environment in <kit>/.venv
#      (nothing is installed into your system Python)
#   3. installs the pinned dependencies from requirements.txt
#   4. merges the 5 servers into your Claude Desktop config
#      (other servers are preserved, a .bak backup is made)
# ============================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_DIR="$(dirname "$SCRIPT_DIR")"
SERVERS_DIR="$KIT_DIR/servers"
VENV_DIR="$KIT_DIR/.venv"

if [[ "$OSTYPE" == darwin* ]]; then
    CLAUDE_CONFIG_DIR="$HOME/Library/Application Support/Claude"
else
    # Claude Desktop has no official Linux build; this is the path used by community builds.
    CLAUDE_CONFIG_DIR="$HOME/.config/Claude"
fi
CLAUDE_CONFIG="$CLAUDE_CONFIG_DIR/claude_desktop_config.json"

echo ""
echo -e "${CYAN}== MCP Starter Kit Vol. 1 - Installer ==${NC}"
echo ""

# ── Step 1: Python 3.10+ ────────────────────────────────────────────────────
echo -e "${YELLOW}[1/4] Checking Python...${NC}"
PYTHON_PKG_VERSION="3.13.14"   # python.org release offered on macOS if no Python 3.10+ exists

_py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }

_find_python() {
    local c
    for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python \
             /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
             /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
             /opt/homebrew/bin/python3 /usr/local/bin/python3; do
        if command -v "$c" &>/dev/null && _py_ok "$c"; then command -v "$c"; return 0; fi
    done
    return 1
}

_install_python_macos() {
    local ver="$PYTHON_PKG_VERSION" pkg url tmp
    pkg="python-${ver}-macos11.pkg"
    url="https://www.python.org/ftp/python/${ver}/${pkg}"
    tmp="$(mktemp -d)"
    command -v curl &>/dev/null || { echo -e "${RED}  curl not found.${NC}"; return 1; }
    echo "  Downloading Python ${ver} from python.org (~70 MB)..."
    curl -fL --progress-bar "$url" -o "$tmp/$pkg" || { rm -rf "$tmp"; return 1; }
    echo -e "  Installing - ${YELLOW}macOS will ask for your login password${NC}:"
    sudo installer -pkg "$tmp/$pkg" -target / || { rm -rf "$tmp"; return 1; }
    rm -rf "$tmp"; hash -r 2>/dev/null || true
}

PYTHON="$(_find_python || true)"
if [ -z "$PYTHON" ] && [[ "$OSTYPE" == darwin* ]]; then
    printf "  No Python 3.10+ found. Install Python %s from python.org now? [Y/n] " "$PYTHON_PKG_VERSION"
    read -r REPLY </dev/tty || REPLY="n"
    if [[ -z "$REPLY" || "$REPLY" =~ ^[Yy] ]]; then _install_python_macos && PYTHON="$(_find_python || true)"; fi
fi
if [ -z "$PYTHON" ]; then
    echo -e "${RED}  No Python 3.10+ available.${NC} Install it, then re-run this script:"
    echo "    macOS: https://www.python.org/downloads/macos/"
    echo "    Linux: sudo apt-get install python3 python3-venv   (or your distro's equivalent)"
    exit 1
fi
echo -e "${GREEN}  OK $("$PYTHON" --version 2>&1) ($PYTHON)${NC}"

# ── Step 2: virtual environment + dependencies ──────────────────────────────
echo ""
echo -e "${YELLOW}[2/4] Creating virtual environment and installing dependencies...${NC}"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    "$PYTHON" -m venv "$VENV_DIR" || {
        echo -e "${RED}  Could not create a venv.${NC} On Debian/Ubuntu: sudo apt-get install python3-venv"; exit 1; }
fi
VPY="$VENV_DIR/bin/python"
"$VPY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
if ! "$VPY" -m pip install --quiet -r "$KIT_DIR/requirements.txt"; then
    echo -e "${RED}  Failed to install core packages.${NC} Check your internet connection and re-run."
    exit 1
fi
echo -e "${GREEN}  OK core packages (mcp, httpx, pydantic, markdownify)${NC}"
if "$VPY" -m pip install --quiet -r "$KIT_DIR/requirements-pdf.txt" 2>/dev/null; then
    echo -e "${GREEN}  OK optional PDF packages${NC}"
else
    echo -e "${YELLOW}  ! optional PDF packages failed - PDF export will save HTML instead${NC}"
fi
if [[ "$OSTYPE" == darwin* ]] && ! "$VPY" -c "import weasyprint" >/dev/null 2>&1; then
    echo -e "${YELLOW}  ! WeasyPrint needs native libraries for real PDFs: brew install pango${NC}"
fi

# ── Step 3: validate servers ────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[3/4] Validating server scripts...${NC}"
for server in "$SERVERS_DIR"/*.py; do
    if "$VPY" -m py_compile "$server" 2>/dev/null; then
        echo -e "${GREEN}  OK $(basename "$server")${NC}"
    else
        echo -e "${RED}  FAIL $(basename "$server")${NC}"; exit 1
    fi
done

# ── Step 4: merge into Claude Desktop config ────────────────────────────────
echo ""
echo -e "${YELLOW}[4/4] Configuring Claude Desktop...${NC}"
mkdir -p "$CLAUDE_CONFIG_DIR"
if [ -f "$CLAUDE_CONFIG" ]; then
    cp "$CLAUDE_CONFIG" "${CLAUDE_CONFIG}.bak"
    echo "  Backup: ${CLAUDE_CONFIG}.bak"
fi

# All JSON is built by Python (no shell string-building), so paths with spaces
# or quotes cannot break the config.
VPY="$VPY" SERVERS_DIR="$SERVERS_DIR" "$VPY" - "$CLAUDE_CONFIG" <<'PYEOF'
import json, os, sys
from pathlib import Path
cfg_path = Path(sys.argv[1])
vpy, sd, home = os.environ["VPY"], Path(os.environ["SERVERS_DIR"]), Path.home()
servers = {
    "file-manager": {"command": vpy, "args": [str(sd / "file_manager_mcp.py")], "env": {"FILE_MANAGER_ROOT": str(home / "Documents")}},
    "web-scraper":  {"command": vpy, "args": [str(sd / "web_scraper_mcp.py")], "env": {}},
    "sqlite":       {"command": vpy, "args": [str(sd / "sqlite_mcp.py")], "env": {"SQLITE_DB_PATH": str(home / "data.db"), "SQLITE_READ_ONLY": "true"}},
    "pdf-export":   {"command": vpy, "args": [str(sd / "pdf_export_mcp.py")], "env": {"PDF_OUTPUT_DIR": str(home / "Desktop")}},
    "notes":        {"command": vpy, "args": [str(sd / "notes_mcp.py")], "env": {"NOTES_FILE": str(home / ".mcp_notes.json")}},
}
config = {}
if cfg_path.exists():
    try:
        config = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("config is not a JSON object")
    except Exception as e:
        sys.exit(f"  Existing config is not valid JSON ({e}). Fix it or add the servers manually "
                 f"from config/claude_desktop_config.template.json. Nothing was changed.")
config.setdefault("mcpServers", {}).update(servers)
cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
print("  OK servers merged (existing servers preserved)")
PYEOF

echo ""
echo -e "${GREEN}Done.${NC} Quit Claude Desktop completely (Cmd+Q) and reopen it."
echo "  Config: $CLAUDE_CONFIG"
echo "  Using Claude Code instead? See README > 'Claude Code'."
echo ""
