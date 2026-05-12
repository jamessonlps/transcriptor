#!/usr/bin/env bash
# Instala Transcriptor no Linux:
#   - launcher em ~/.local/bin/transcriptor-launcher
#   - atalho em ~/.local/share/applications/transcriptor.desktop
#   - ícone PNG em ~/.local/share/icons/transcriptor.png

set -euo pipefail

PROJECT_DIR="${1:-}"
if [ -z "$PROJECT_DIR" ] || [ ! -f "$PROJECT_DIR/run.sh" ]; then
    echo "ERRO: PROJECT_DIR inválido: $PROJECT_DIR"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
ICONS_DIR="$HOME/.local/share/icons"

mkdir -p "$BIN_DIR" "$APPS_DIR" "$ICONS_DIR"

LAUNCHER="$BIN_DIR/transcriptor-launcher"
DESKTOP="$APPS_DIR/transcriptor.desktop"
ICON_PNG="$ICONS_DIR/transcriptor.png"

# ---------- Launcher ----------
cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/usr/bin/env bash
# Transcriptor — Linux launcher (sem terminal visível).
set -e

PROJECT_DIR="$PROJECT_DIR"
PORT="\${TRANSCRIPTOR_PORT:-8765}"
URL="http://localhost:\${PORT}"
LOG_FILE="\${XDG_STATE_HOME:-\$HOME/.local/state}/transcriptor.log"

mkdir -p "\$(dirname "\$LOG_FILE")"
log() { echo "[\$(date '+%H:%M:%S')] \$*" >> "\$LOG_FILE"; }

log "Launcher iniciado"

open_browser() {
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "\$URL" >/dev/null 2>&1 &
    elif command -v gnome-open >/dev/null 2>&1; then
        gnome-open "\$URL" >/dev/null 2>&1 &
    else
        log "Nenhum 'xdg-open' disponível — abra \$URL manualmente"
    fi
}

# Já está rodando? Só abre o browser.
if curl -fs --max-time 1 "\${URL}/api/health" >/dev/null 2>&1; then
    log "Servidor já está rodando; só abrindo browser"
    open_browser
    exit 0
fi

cd "\$PROJECT_DIR"
PORT="\$PORT" ./run.sh >> "\$LOG_FILE" 2>&1 &
SERVER_PID=\$!
log "Servidor iniciado (pid=\$SERVER_PID)"

cleanup() {
    log "Encerrando servidor..."
    kill -TERM "\$SERVER_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
        kill -0 "\$SERVER_PID" 2>/dev/null || break
        sleep 1
    done
    kill -KILL "\$SERVER_PID" 2>/dev/null || true
}
trap cleanup TERM INT EXIT

# Espera porta
for i in \$(seq 1 120); do
    if curl -fs --max-time 1 "\${URL}/api/health" >/dev/null 2>&1; then
        log "Servidor pronto"
        break
    fi
    if ! kill -0 "\$SERVER_PID" 2>/dev/null; then
        log "Servidor morreu antes de subir"
        if command -v notify-send >/dev/null 2>&1; then
            notify-send "Transcriptor" "Falha ao iniciar. Veja \$LOG_FILE"
        fi
        exit 1
    fi
    sleep 0.5
done

open_browser
wait "\$SERVER_PID"
LAUNCHER_EOF

chmod +x "$LAUNCHER"

# ---------- .desktop ----------
cat > "$DESKTOP" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Name=Transcriptor
GenericName=Local Transcription
Comment=Transcrição local de áudio/vídeo em pt-br
Exec=$LAUNCHER
Icon=$ICON_PNG
Terminal=false
Categories=AudioVideo;Audio;Utility;
Keywords=transcription;audio;whisper;subtitles;
StartupNotify=true
StartupWMClass=transcriptor
DESKTOP_EOF

chmod +x "$DESKTOP"

# ---------- Ícone (best-effort) ----------
SVG_SRC="$SCRIPT_DIR/../macos/icon.svg"
if [ -f "$SVG_SRC" ]; then
    if command -v rsvg-convert >/dev/null 2>&1; then
        rsvg-convert -w 512 -h 512 "$SVG_SRC" -o "$ICON_PNG" 2>/dev/null && echo "  ícone: ok (rsvg-convert)" || true
    elif command -v inkscape >/dev/null 2>&1; then
        inkscape "$SVG_SRC" --export-type=png --export-filename="$ICON_PNG" --export-width=512 2>/dev/null && echo "  ícone: ok (inkscape)" || true
    elif command -v convert >/dev/null 2>&1; then
        # ImageMagick (qualidade pior em SVG, mas funciona)
        convert -background none -resize 512x512 "$SVG_SRC" "$ICON_PNG" 2>/dev/null && echo "  ícone: ok (imagemagick)" || true
    else
        echo "  ícone: pulado (instale rsvg-convert, inkscape ou imagemagick)"
    fi
fi

# Atualiza o cache de aplicações (gnome/kde)
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
fi

echo ""
echo "=========================================="
echo "  Transcriptor instalado!"
echo "  Launcher:    $LAUNCHER"
echo "  Atalho:      $DESKTOP"
echo "  Logs:        ~/.local/state/transcriptor.log"
echo ""
echo "  Abra o menu de aplicativos do seu sistema"
echo "  e busque por 'Transcriptor'."
echo "=========================================="
