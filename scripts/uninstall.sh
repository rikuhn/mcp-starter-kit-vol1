#!/usr/bin/env bash
# ============================================================
#  MCP Starter Kit Vol. 1 - Uninstaller (macOS / Linux)
#  Removes the kit's entries from the Claude Desktop config and,
#  after asking, the notes file and the kit folder (incl. .venv).
#  https://github.com/rikuhn/mcp-starter-kit-vol1
# ============================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_DIR="$(dirname "$SCRIPT_DIR")"
SERVER_KEYS="file-manager web-scraper sqlite pdf-export notes"

if [[ "$OSTYPE" == darwin* ]]; then
    CLAUDE_CONFIG_DIR="$HOME/Library/Application Support/Claude"
else
    CLAUDE_CONFIG_DIR="$HOME/.config/Claude"
fi
CLAUDE_CONFIG="$CLAUDE_CONFIG_DIR/claude_desktop_config.json"

# Any python works for editing JSON; prefer the kit's own venv.
PYTHON=""
for c in "$KIT_DIR/.venv/bin/python" python3 python; do
    if command -v "$c" &>/dev/null || [ -x "$c" ]; then PYTHON="$c"; break; fi
done

_ask() { printf "%b " "$1"; read -r REPLY </dev/tty 2>/dev/null || REPLY=""; [ -z "$REPLY" ] && REPLY="$2"; return 0; }

echo ""
echo -e "${CYAN}== MCP Starter Kit Vol. 1 - Uninstaller ==${NC}"

# ── Step 1: remove server entries (and remember the notes path) ─────────────
echo -e "\n${YELLOW}[1/3] Removing servers from Claude Desktop config...${NC}"
NOTES_FILE=""
if [ ! -f "$CLAUDE_CONFIG" ]; then
    echo "  No config found at $CLAUDE_CONFIG - nothing to clean."
elif [ -z "$PYTHON" ]; then
    echo -e "  ${RED}Python not found${NC} - remove these keys from mcpServers manually: $SERVER_KEYS"
else
    cp "$CLAUDE_CONFIG" "${CLAUDE_CONFIG}.bak"
    OUT="$(KEYS="$SERVER_KEYS" "$PYTHON" - "$CLAUDE_CONFIG" <<'PYEOF'
import json, os, sys
path = sys.argv[1]
try:
    cfg = json.load(open(path, encoding="utf-8"))
except Exception:
    print("|"); sys.exit(0)
servers = cfg.get("mcpServers", {})
notes = servers.get("notes", {}).get("env", {}).get("NOTES_FILE", "")
removed = [k for k in os.environ["KEYS"].split() if servers.pop(k, None) is not None]
cfg["mcpServers"] = servers
json.dump(cfg, open(path, "w", encoding="utf-8"), indent=2)
print(",".join(removed) + "|" + notes)
PYEOF
)"
    REMOVED="${OUT%%|*}"; NOTES_FILE="${OUT#*|}"
    if [ -n "$REMOVED" ]; then
        echo -e "${GREEN}  OK removed: ${REMOVED//,/, }${NC} (backup: ${CLAUDE_CONFIG}.bak)"
    else
        echo "  No MCP Starter Kit servers were present in the config."
    fi
fi

# ── Step 2: optional - notes data ───────────────────────────────────────────
echo -e "\n${YELLOW}[2/3] Notes data...${NC}"
[ -z "$NOTES_FILE" ] && NOTES_FILE="$HOME/.mcp_notes.json"
if [ -f "$NOTES_FILE" ]; then
    _ask "  Delete your saved notes file ($NOTES_FILE)? [y/N]" "N"
    if [[ "$REPLY" =~ ^[Yy] ]]; then rm -f "$NOTES_FILE" && echo -e "${GREEN}  OK notes file deleted${NC}"; else echo "  Kept: $NOTES_FILE"; fi
else
    echo "  No notes file found."
fi

# ── Step 3: optional - kit folder incl. .venv (with safety guard) ───────────
echo -e "\n${YELLOW}[3/3] Kit files...${NC}"
if [ ! -d "$KIT_DIR/servers" ] || [ ! -d "$KIT_DIR/scripts" ] \
   || [ "$KIT_DIR" = "$HOME" ] || [ "$KIT_DIR" = "/" ]; then
    echo -e "  ${YELLOW}Skipping - '$KIT_DIR' does not look like the kit folder.${NC}"
else
    _ask "  Delete the kit folder ($KIT_DIR) including its .venv? [y/N]" "N"
    if [[ "$REPLY" =~ ^[Yy] ]]; then
        rm -rf "$KIT_DIR" && echo -e "${GREEN}  OK kit folder removed${NC}"
    else
        echo "  Kept: $KIT_DIR (delete $KIT_DIR/.venv to free the Python packages)"
    fi
fi

echo -e "\n${GREEN}Done.${NC} Restart Claude Desktop (Cmd+Q, then reopen) to apply the changes.\n"
