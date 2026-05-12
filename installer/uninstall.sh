#!/usr/bin/env bash
# Transcriptor uninstaller — remove apenas o atalho do sistema.
# Não toca no diretório do projeto.

set -euo pipefail

case "$(uname -s)" in
    Darwin)
        APP="$HOME/Applications/Transcriptor.app"
        if [ -d "$APP" ]; then
            rm -rf "$APP"
            echo "Removido: $APP"
        else
            echo "Transcriptor.app não encontrado em $HOME/Applications/"
        fi
        # Para o servidor se estiver rodando
        if pgrep -f "uvicorn app.main:app" >/dev/null 2>&1; then
            pkill -f "uvicorn app.main:app" || true
            echo "Servidor parado."
        fi
        ;;
    Linux)
        DESKTOP="$HOME/.local/share/applications/transcriptor.desktop"
        ICON="$HOME/.local/share/icons/transcriptor.png"
        BIN="$HOME/.local/bin/transcriptor-launcher"
        for f in "$DESKTOP" "$ICON" "$BIN"; do
            if [ -f "$f" ]; then
                rm -f "$f"
                echo "Removido: $f"
            fi
        done
        if pgrep -f "uvicorn app.main:app" >/dev/null 2>&1; then
            pkill -f "uvicorn app.main:app" || true
            echo "Servidor parado."
        fi
        # Atualiza o menu (se gnome/kde)
        if command -v update-desktop-database >/dev/null 2>&1; then
            update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
        fi
        ;;
    *)
        echo "Para desinstalar no Windows, execute em PowerShell:"
        echo "  powershell -ExecutionPolicy Bypass -File installer\\windows\\uninstall.ps1"
        exit 1
        ;;
esac

echo ""
echo "Pronto."
