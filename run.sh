#!/usr/bin/env bash
# Transcriptor - setup + run em um comando.
# Idempotente: cria venv se nao existir, instala deps se faltar, e sobe o servidor.

set -euo pipefail

cd "$(dirname "$0")"

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ERRO: ffmpeg nao encontrado."
    echo "  macOS:  brew install ffmpeg"
    echo "  Ubuntu: sudo apt install ffmpeg"
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo ">>> Criando virtualenv em .venv..."
    python3 -m venv .venv
fi

source .venv/bin/activate

if ! python -c "import fastapi, faster_whisper" 2>/dev/null; then
    echo ">>> Instalando dependencias..."
    pip install --upgrade pip --quiet
    pip install -e . --quiet
fi

PORT="${PORT:-8765}"
echo ""
echo "=========================================="
echo "  Transcriptor pronto"
echo "  URL: http://localhost:${PORT}"
echo "=========================================="
echo ""

exec uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" --timeout-keep-alive 600
