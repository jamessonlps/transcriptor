# Transcriptor — Windows uninstaller.

$ErrorActionPreference = 'SilentlyContinue'

$AppRoot   = Join-Path $env:LOCALAPPDATA "Transcriptor"
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$LnkStart  = Join-Path $StartMenu "Transcriptor.lnk"
$DesktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) "Transcriptor.lnk"

# Mata uvicorn se estiver rodando
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -match 'uvicorn app.main:app'
} | Stop-Process -Force

# Remove artefatos
foreach ($p in @($AppRoot, $LnkStart, $DesktopLnk)) {
    if (Test-Path $p) {
        Remove-Item -Recurse -Force $p
        Write-Host "Removido: $p"
    }
}

Write-Host ""
Write-Host "Transcriptor desinstalado."
