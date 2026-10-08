# ============================================================
#  MCP Starter Kit Vol. 1 - Installer (Windows / PowerShell 5.1+)
#  https://github.com/rikuhn/mcp-starter-kit-vol1
#
#  Creates a private virtual environment in <kit>\.venv, installs the
#  pinned dependencies and merges the 5 servers into the Claude Desktop
#  config (other servers preserved, .bak backup).
# ============================================================
#Requires -Version 5.1
Set-StrictMode -Version Latest
# "Continue", not "Stop": in Windows PowerShell 5.1 any stderr output of a native
# command (pip warnings!) becomes a terminating error under "Stop". Native exit
# codes are checked explicitly via $LASTEXITCODE; cmdlets use -ErrorAction Stop.
$ErrorActionPreference = "Continue"

$KitDir     = Split-Path (Split-Path $MyInvocation.MyCommand.Path -Parent) -Parent
$ServersDir = Join-Path $KitDir "servers"
$VenvDir    = Join-Path $KitDir ".venv"
$ConfigDir  = Join-Path $env:APPDATA "Claude"
$ConfigPath = Join-Path $ConfigDir "claude_desktop_config.json"

function Write-Step { param($n, $msg) Write-Host "`n[$n/4] $msg" -ForegroundColor Yellow }
function Write-OK   { param($msg) Write-Host "  OK $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  !  $msg" -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host "  X  $msg" -ForegroundColor Red }

Write-Host "`n== MCP Starter Kit Vol. 1 - Installer (Windows) ==" -ForegroundColor Cyan

# ── Step 1: Python 3.10+ ────────────────────────────────────────────────────
Write-Step 1 "Checking Python..."
$Python = $null
foreach ($cmd in @("py", "python", "python3")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { continue }
    & $cmd -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $Python = $cmd; break }
}
if (-not $Python) {
    Write-Fail "No Python 3.10+ found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH'), then re-run."
    exit 1
}
Write-OK "$(& $Python --version)"

# ── Step 2: venv + dependencies ─────────────────────────────────────────────
Write-Step 2 "Creating virtual environment and installing dependencies..."
$Vpy = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $Vpy)) {
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Write-Fail "Could not create the virtual environment."; exit 1 }
}
& $Vpy -m pip install --quiet --upgrade pip 2>$null | Out-Null
& $Vpy -m pip install --quiet -r (Join-Path $KitDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { Write-Fail "Failed to install core packages."; exit 1 }
Write-OK "Core packages installed"
& $Vpy -m pip install --quiet -r (Join-Path $KitDir "requirements-pdf.txt") 2>$null
if ($LASTEXITCODE -eq 0) { Write-OK "Optional PDF packages installed" }
else { Write-Warn "Optional PDF packages failed - PDF export will save HTML instead" }

# ── Step 3: validate servers ────────────────────────────────────────────────
Write-Step 3 "Validating server scripts..."
foreach ($f in Get-ChildItem -Path $ServersDir -Filter "*.py") {
    & $Vpy -m py_compile $f.FullName 2>$null
    if ($LASTEXITCODE -eq 0) { Write-OK $f.Name } else { Write-Fail $f.Name; exit 1 }
}

# ── Step 4: merge into Claude Desktop config (done in Python for valid JSON) ─
Write-Step 4 "Configuring Claude Desktop..."
if (-not (Test-Path $ConfigDir)) { New-Item -ItemType Directory -Path $ConfigDir -Force -ErrorAction Stop | Out-Null }
if (Test-Path $ConfigPath) { Copy-Item $ConfigPath "$ConfigPath.bak" -Force -ErrorAction Stop; Write-Host "  Backup: $ConfigPath.bak" }

$merge = @'
import json, os, sys
from pathlib import Path
cfg_path, vpy, sd = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
home = Path(os.environ["USERPROFILE"])
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
        config = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        if not isinstance(config, dict):
            raise ValueError("config is not a JSON object")
    except Exception as e:
        sys.exit(f"Existing config is not valid JSON ({e}). Nothing was changed.")
config.setdefault("mcpServers", {}).update(servers)
cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")  # UTF-8 without BOM
print("  OK servers merged (existing servers preserved)")
'@
$tmp = Join-Path $env:TEMP "mcp_kit_merge.py"
[System.IO.File]::WriteAllText($tmp, $merge, (New-Object System.Text.UTF8Encoding $false))
& $Vpy $tmp $ConfigPath $Vpy $ServersDir
$rc = $LASTEXITCODE
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
if ($rc -ne 0) { Write-Fail "Config merge failed - see message above."; exit 1 }

Write-Host "`nDone. Quit Claude Desktop completely (tray icon > Quit) and reopen it." -ForegroundColor Green
Write-Host "  Config: $ConfigPath"
