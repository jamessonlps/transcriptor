# Transcriptor — Windows installer (PowerShell).
# Cria:
#   - launcher .bat + wrapper .vbs (sem janela visível) em %LOCALAPPDATA%\Transcriptor\
#   - atalho .lnk no Start Menu (e opcional na Área de Trabalho)
# Uso:
#   powershell -ExecutionPolicy Bypass -File installer\windows\install.ps1
#   powershell -ExecutionPolicy Bypass -File installer\windows\install.ps1 -Desktop  # com atalho na desktop

[CmdletBinding()]
param(
    [switch]$Desktop,
    [string]$ProjectDir = ""
)

$ErrorActionPreference = 'Stop'

# Resolve o diretório do projeto
if (-not $ProjectDir) {
    $ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
if (-not (Test-Path (Join-Path $ProjectDir "run.sh"))) {
    Write-Error "ProjectDir inválido (não tem run.sh): $ProjectDir"
    exit 1
}

# Diretórios alvo
$AppRoot   = Join-Path $env:LOCALAPPDATA "Transcriptor"
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$LogDir    = Join-Path $env:LOCALAPPDATA "Transcriptor\logs"

New-Item -ItemType Directory -Force -Path $AppRoot, $StartMenu, $LogDir | Out-Null

$BatPath  = Join-Path $AppRoot "transcriptor-launcher.bat"
$VbsPath  = Join-Path $AppRoot "transcriptor-launcher.vbs"
$IcoPath  = Join-Path $AppRoot "transcriptor.ico"
$LnkStart = Join-Path $StartMenu "Transcriptor.lnk"

# ---------- Launcher .bat ----------
# Idéia: ativa o venv, sobe uvicorn em background, espera porta, abre browser.
# O .bat é chamado pelo .vbs com janela escondida (WindowStyle=0).
$batScript = @"
@echo off
setlocal enabledelayedexpansion

set PROJECT_DIR=$ProjectDir
set PORT=8765
if not "%TRANSCRIPTOR_PORT%"=="" set PORT=%TRANSCRIPTOR_PORT%
set URL=http://localhost:%PORT%
set LOG=%LOCALAPPDATA%\Transcriptor\logs\transcriptor.log

cd /d "%PROJECT_DIR%"

REM Se servidor já está rodando, só abre o browser e sai.
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri '%URL%/api/health' -UseBasicParsing -TimeoutSec 1).StatusCode } catch { exit 1 }" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    start "" "%URL%"
    exit /b 0
)

REM Cria venv se não existir
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)

REM Ativa venv
call .venv\Scripts\activate.bat

REM Instala deps se faltarem
python -c "import fastapi, faster_whisper" 2>nul
if errorlevel 1 (
    python -m pip install --upgrade pip --quiet
    python -m pip install -e . --quiet
)

REM Sobe o servidor (uvicorn em foreground; .vbs garante que a janela do CMD fica oculta)
echo [%TIME%] Iniciando servidor em http://localhost:%PORT% >> "%LOG%" 2>&1

REM Inicia uvicorn em background e captura o PID para depois mesclá-lo com o do .vbs.
start /B "" python -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --timeout-keep-alive 600 >> "%LOG%" 2>&1

REM Espera o servidor responder
set TRIES=0
:waitloop
set /a TRIES+=1
if %TRIES% GTR 120 goto :failed
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri '%URL%/api/health' -UseBasicParsing -TimeoutSec 1).StatusCode } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto :waitloop
)

start "" "%URL%"

REM Mantém o .bat vivo enquanto o python rodar. Quando o usuário fechar pela bandeja
REM (ou via taskkill), o servidor também morre.
:keepalive
tasklist /FI "IMAGENAME eq python.exe" 2>nul | find /I "python.exe" >nul
if errorlevel 1 goto :end
timeout /t 5 /nobreak >nul
goto :keepalive

:failed
echo [%TIME%] Servidor falhou ao iniciar — veja %LOG% >> "%LOG%"
msg %USERNAME% /TIME:10 "Transcriptor: falha ao iniciar. Veja %LOG%"
exit /b 1

:end
exit /b 0
"@
$batScript | Set-Content -Path $BatPath -Encoding ASCII

# ---------- Wrapper .vbs (esconde a janela) ----------
$vbsScript = @"
Set WshShell = CreateObject("WScript.Shell")
' Run com WindowStyle=0 esconde completamente a janela do cmd.
WshShell.Run Chr(34) & "$BatPath" & Chr(34), 0, False
"@
$vbsScript | Set-Content -Path $VbsPath -Encoding ASCII

# ---------- Ícone ----------
# Tenta converter o SVG (mesmo do macOS) para .ico via Inkscape se disponível.
# Sem Inkscape, deixa o atalho usar um ícone padrão do sistema.
$SvgSrc = Join-Path $PSScriptRoot "..\macos\icon.svg"
if ((Test-Path $SvgSrc) -and (Get-Command inkscape -ErrorAction SilentlyContinue)) {
    $TmpPng = Join-Path $env:TEMP "transcriptor-icon.png"
    & inkscape $SvgSrc --export-type=png --export-filename=$TmpPng --export-width=256 2>$null
    if (Test-Path $TmpPng) {
        # Converte PNG -> ICO via .NET
        Add-Type -AssemblyName System.Drawing
        $bmp = [System.Drawing.Bitmap]::FromFile($TmpPng)
        $icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
        $fs = [System.IO.File]::Create($IcoPath)
        $icon.Save($fs); $fs.Close()
        $bmp.Dispose(); $icon.Dispose()
        Write-Host "  ícone: ok"
    }
}

# ---------- Atalho .lnk ----------
function New-Shortcut($lnkPath, $target, $icon, $description) {
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($lnkPath)
    $sc.TargetPath = "wscript.exe"
    $sc.Arguments  = "`"$target`""
    $sc.WorkingDirectory = $ProjectDir
    $sc.Description = $description
    if ($icon -and (Test-Path $icon)) { $sc.IconLocation = $icon }
    $sc.WindowStyle = 7  # minimizada — mas wscript já não abre janela
    $sc.Save()
}

New-Shortcut -lnkPath $LnkStart -target $VbsPath -icon $IcoPath -description "Transcriptor — local transcription"

if ($Desktop) {
    $DesktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) "Transcriptor.lnk"
    New-Shortcut -lnkPath $DesktopLnk -target $VbsPath -icon $IcoPath -description "Transcriptor — local transcription"
    Write-Host "  atalho da Área de Trabalho: $DesktopLnk"
}

Write-Host ""
Write-Host "=========================================="
Write-Host "  Transcriptor instalado!"
Write-Host "  Launcher: $VbsPath"
Write-Host "  Atalho:   $LnkStart"
Write-Host "  Logs:     $LogDir\transcriptor.log"
Write-Host ""
Write-Host "  Pressione Win e busque por 'Transcriptor'."
Write-Host "=========================================="
