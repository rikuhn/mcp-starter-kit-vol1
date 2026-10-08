# ============================================================
#  MCP Starter Kit Vol. 1 - Uninstaller (Windows / PowerShell 5.1+)
#  Removes the kit's entries from the Claude Desktop config and,
#  after asking, the notes file and the kit folder (incl. .venv).
#  https://github.com/rikuhn/mcp-starter-kit-vol1
# ============================================================
#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"   # see install.ps1 for the reason

function Write-OK   { param($m) Write-Host "  OK $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "  !  $m" -ForegroundColor Yellow }
function Write-Step { param($n,$m) Write-Host "`n[$n/3] $m" -ForegroundColor Cyan }
function Ask { param($q,$def) $r = Read-Host $q; if ([string]::IsNullOrWhiteSpace($r)) { return $def }; return $r }

$KitDir     = Split-Path (Split-Path $MyInvocation.MyCommand.Path -Parent) -Parent
$ConfigPath = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
$ServerKeys = @("file-manager","web-scraper","sqlite","pdf-export","notes")

Write-Host "`n== MCP Starter Kit Vol. 1 - Uninstaller ==" -ForegroundColor Cyan

# ── Step 1: config entries ──────────────────────────────────────────────────
Write-Step 1 "Removing servers from Claude Desktop config..."
$notesFile = $null
if (Test-Path $ConfigPath) {
    Copy-Item $ConfigPath "$ConfigPath.bak" -Force -ErrorAction Stop
    $cfg = Get-Content $ConfigPath -Raw -ErrorAction Stop | ConvertFrom-Json
    if (($cfg.PSObject.Properties.Name -contains "mcpServers") -and $cfg.mcpServers) {
        $notes = $cfg.mcpServers.PSObject.Properties["notes"]
        if ($notes -and $notes.Value.PSObject.Properties["env"] -and $notes.Value.env.PSObject.Properties["NOTES_FILE"]) {
            $notesFile = $notes.Value.env.NOTES_FILE
        }
        $removed = @()
        foreach ($k in $ServerKeys) {
            if ($cfg.mcpServers.PSObject.Properties.Name -contains $k) { $cfg.mcpServers.PSObject.Properties.Remove($k); $removed += $k }
        }
        $json = $cfg | ConvertTo-Json -Depth 20
        [System.IO.File]::WriteAllText($ConfigPath, $json, (New-Object System.Text.UTF8Encoding $false))
        if ($removed.Count -gt 0) { Write-OK ("Removed: " + ($removed -join ", ") + " (backup: $ConfigPath.bak)") }
        else { Write-Warn "No MCP Starter Kit servers were present in the config." }
    } else { Write-Warn "Config has no mcpServers section." }
} else { Write-Warn "No config found at $ConfigPath." }

# ── Step 2: notes data ──────────────────────────────────────────────────────
Write-Step 2 "Notes data..."
if (-not $notesFile) { $notesFile = Join-Path $env:USERPROFILE ".mcp_notes.json" }
if (Test-Path $notesFile) {
    if ((Ask "  Delete your saved notes file ($notesFile)? [y/N]" "N") -match '^[Yy]') { Remove-Item $notesFile -Force; Write-OK "Notes file deleted" }
    else { Write-Host "  Kept: $notesFile" }
} else { Write-Host "  No notes file found." }

# ── Step 3: kit folder incl. .venv (with safety guard) ──────────────────────
Write-Step 3 "Kit files..."
$root = [System.IO.Path]::GetPathRoot($KitDir)
$looksLikeKit = (Test-Path (Join-Path $KitDir "servers")) -and (Test-Path (Join-Path $KitDir "scripts"))
if ((-not $looksLikeKit) -or ($KitDir -eq $env:USERPROFILE) -or ($KitDir -eq $root)) {
    Write-Warn "Skipping - '$KitDir' does not look like the kit folder."
} elseif ((Ask "  Delete the kit folder ($KitDir) including its .venv? [y/N]" "N") -match '^[Yy]') {
    Set-Location $env:USERPROFILE
    Remove-Item $KitDir -Recurse -Force -ErrorAction Stop
    Write-OK "Kit folder removed"
} else { Write-Host "  Kept: $KitDir" }

Write-Host "`nDone. Restart Claude Desktop to apply the changes." -ForegroundColor Green
