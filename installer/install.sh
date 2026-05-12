#!/usr/bin/env bash
# Transcriptor installer — entry point.
# Detecta o OS e delega para o instalador apropriado.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

case "$(uname -s)" in
    Darwin)
        echo ">>> Instalando Transcriptor.app no macOS..."
        exec "$SCRIPT_DIR/macos/build-app.sh" "$PROJECT_DIR"
        ;;
    Linux)
        echo ">>> Instalando Transcriptor no Linux..."
        exec "$SCRIPT_DIR/linux/install.sh" "$PROJECT_DIR"
        ;;
    MINGW*|MSYS*|CYGWIN*)
        echo "Para Windows, use PowerShell e execute:"
        echo "  powershell -ExecutionPolicy Bypass -File installer\\windows\\install.ps1"
        exit 1
        ;;
    *)
        echo "Sistema operacional não suportado: $(uname -s)"
        exit 1
        ;;
esac
