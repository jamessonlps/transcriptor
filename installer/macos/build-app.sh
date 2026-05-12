#!/usr/bin/env bash
# Gera Transcriptor.app em ~/Applications apontando para o projeto local.
# Idempotente: pode rodar várias vezes que reconstrói tudo do zero.

set -euo pipefail

PROJECT_DIR="${1:-}"
if [ -z "$PROJECT_DIR" ] || [ ! -f "$PROJECT_DIR/run.sh" ]; then
    echo "ERRO: PROJECT_DIR inválido: $PROJECT_DIR"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/Applications/Transcriptor.app"
CONTENTS="$APP_DIR/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"

# Remove versão anterior
rm -rf "$APP_DIR"
mkdir -p "$MACOS" "$RESOURCES"

# ---------- Info.plist ----------
cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Transcriptor</string>
    <key>CFBundleDisplayName</key>
    <string>Transcriptor</string>
    <key>CFBundleIdentifier</key>
    <string>com.transcriptor.local</string>
    <key>CFBundleVersion</key>
    <string>0.2.0</string>
    <key>CFBundleShortVersionString</key>
    <string>0.2.0</string>
    <key>CFBundleExecutable</key>
    <string>transcriptor</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>icon</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
    <key>LSUIElement</key>
    <false/>
    <key>NSHumanReadableCopyright</key>
    <string>Transcriptor — local-first transcription</string>
</dict>
</plist>
PLIST

# ---------- Launcher (executável) ----------
cat > "$MACOS/transcriptor" <<LAUNCHER
#!/usr/bin/env bash
# Launcher do Transcriptor.app — sobe o servidor (se não estiver rodando) e
# abre o navegador. Mantém o processo do app vivo enquanto o servidor roda
# para que "Quit" no menu do Dock também encerre o servidor.

set -e

PROJECT_DIR="$PROJECT_DIR"
PORT="\${TRANSCRIPTOR_PORT:-8765}"
URL="http://localhost:\${PORT}"
LOG_FILE="\$HOME/Library/Logs/Transcriptor.log"

mkdir -p "\$(dirname "\$LOG_FILE")"

log() { echo "[\$(date '+%H:%M:%S')] \$*" >> "\$LOG_FILE"; }

log "Launcher iniciado — PROJECT_DIR=\$PROJECT_DIR PORT=\$PORT"

# Se servidor já está rodando, só abre o browser e sai.
if curl -fs --max-time 1 "\${URL}/api/health" >/dev/null 2>&1; then
    log "Servidor já está rodando; abrindo browser"
    open "\$URL"
    exit 0
fi

cd "\$PROJECT_DIR"

# Sobe o servidor em background. run.sh já faz exec uvicorn, então o PID
# capturado abaixo é o do uvicorn em si.
PORT="\$PORT" ./run.sh >> "\$LOG_FILE" 2>&1 &
SERVER_PID=\$!
log "Servidor iniciado em background (pid=\$SERVER_PID)"

cleanup() {
    log "Encerrando servidor (pid=\$SERVER_PID)..."
    kill -TERM "\$SERVER_PID" 2>/dev/null || true
    # Aguarda até 5s para encerrar limpo, senão força.
    for _ in 1 2 3 4 5; do
        if ! kill -0 "\$SERVER_PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    kill -KILL "\$SERVER_PID" 2>/dev/null || true
    log "Servidor encerrado"
    exit 0
}
trap cleanup TERM INT EXIT

# Espera até /api/health responder (timeout 60s).
for i in \$(seq 1 120); do
    if curl -fs --max-time 1 "\${URL}/api/health" >/dev/null 2>&1; then
        log "Servidor pronto"
        break
    fi
    if ! kill -0 "\$SERVER_PID" 2>/dev/null; then
        log "Servidor morreu antes de subir — veja \$LOG_FILE"
        osascript -e 'display alert "Transcriptor" message "O servidor falhou ao iniciar. Veja ~/Library/Logs/Transcriptor.log para detalhes."' >/dev/null 2>&1 || true
        exit 1
    fi
    sleep 0.5
done

open "\$URL"
log "Browser aberto em \$URL"

# Mantém o launcher vivo até o servidor morrer (ou o app receber Quit).
wait "\$SERVER_PID"
LAUNCHER

chmod +x "$MACOS/transcriptor"

# ---------- Ícone (best-effort) ----------
build_icon() {
    local svg="$SCRIPT_DIR/icon.svg"
    local tmp
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' RETURN

    if [ ! -f "$svg" ]; then
        echo "  (sem icon.svg em $SCRIPT_DIR — pulando ícone)"
        return 1
    fi

    # Tenta rasterizar o SVG em 1024×1024.
    # Estratégia 1: rsvg-convert (homebrew).
    # Estratégia 2: qlmanage (vem com o macOS, mas é mais limitado).
    local base="$tmp/icon-1024.png"
    if command -v rsvg-convert >/dev/null 2>&1; then
        rsvg-convert -w 1024 -h 1024 "$svg" -o "$base" 2>/dev/null || return 1
    elif command -v qlmanage >/dev/null 2>&1; then
        qlmanage -t -s 1024 -o "$tmp" "$svg" >/dev/null 2>&1 || return 1
        # qlmanage gera "<nome>.png", procuramos o resultado:
        local generated
        generated="$(find "$tmp" -maxdepth 1 -name '*.png' | head -n 1)"
        if [ -z "$generated" ]; then return 1; fi
        mv "$generated" "$base"
    else
        return 1
    fi

    local iconset="$tmp/icon.iconset"
    mkdir -p "$iconset"
    for sz in 16 32 64 128 256 512; do
        sips -z "$sz" "$sz" "$base" --out "$iconset/icon_${sz}x${sz}.png" >/dev/null 2>&1
        local sz2=$((sz * 2))
        sips -z "$sz2" "$sz2" "$base" --out "$iconset/icon_${sz}x${sz}@2x.png" >/dev/null 2>&1
    done
    sips -z 1024 1024 "$base" --out "$iconset/icon_512x512@2x.png" >/dev/null 2>&1

    iconutil -c icns "$iconset" -o "$RESOURCES/icon.icns" 2>/dev/null || return 1
    return 0
}

echo ">>> Construindo bundle em $APP_DIR..."
if build_icon; then
    echo "  ícone: ok"
else
    echo "  ícone: pulado (sem rsvg-convert/qlmanage funcional)"
fi

# Toque no diretório do app para o Finder reconhecer alterações
touch "$APP_DIR"

echo ""
echo "=========================================="
echo "  Transcriptor.app instalado!"
echo "  Local: $APP_DIR"
echo "  Logs:  ~/Library/Logs/Transcriptor.log"
echo ""
echo "  Abra Launchpad ou Spotlight (Cmd+Space)"
echo "  e busque por 'Transcriptor'."
echo "=========================================="
