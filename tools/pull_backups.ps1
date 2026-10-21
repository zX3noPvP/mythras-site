# Download the newest nightly backup from the VPS to this PC (an off-site copy).
# Run it from PowerShell:   powershell -ExecutionPolicy Bypass -File tools\pull_backups.ps1
# It asks for the root password unless you set up an SSH key. Keeps the newest 30 copies in Documents\MythrasBackups.
# The backups contain secrets (tokens, license signing key): keep this folder private, never share or upload it.
param(
    [string]$Server = "root@85.155.151.23",
    [string]$Target = (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "MythrasBackups"),
    [int]$Keep = 30
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $Target | Out-Null

$latest = (ssh $Server "ls -1t /var/backups/mythras-site/nightly-*.tgz 2>/dev/null | head -1").Trim()
if (-not $latest) {
    Write-Host "No nightly backup on the VPS yet. Run there: bash /opt/mythras-site/deploy/backup.sh"
    exit 1
}
$name = Split-Path $latest -Leaf
$dest = Join-Path $Target $name
if (Test-Path $dest) {
    Write-Host "Already downloaded: $dest"
} else {
    scp "${Server}:$latest" "$dest"
    Write-Host "Saved $dest"
}
Get-ChildItem $Target -Filter "nightly-*.tgz" | Sort-Object LastWriteTime -Descending | Select-Object -Skip $Keep | Remove-Item -Force
