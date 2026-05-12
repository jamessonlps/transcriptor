# Transcriptor

Web app local para transcrição de vídeos/áudios em pt-br. Roda 100% no seu computador (nada sai pra nuvem) usando [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## Pré-requisitos

- Python 3.10+
- ffmpeg
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: baixe em https://ffmpeg.org/download.html

## Como rodar

### Modo 1 — Linha de comando

```bash
./run.sh
```

O script é idempotente: cria o virtualenv, instala dependências (só na primeira vez) e sobe o servidor em `http://localhost:8765`.

Para usar outra porta:

```bash
PORT=9000 ./run.sh
```

### Modo 2 — Instalar como app no sistema

Cria um ícone no Launchpad/Menu Iniciar/Aplicações que sobe o servidor sem terminal visível e abre o navegador automaticamente.

**macOS:**

```bash
./installer/install.sh
```

Cria `~/Applications/Transcriptor.app`. Abra pelo Spotlight (`Cmd+Space → Transcriptor`) ou pelo Launchpad. Quando você "sair" (Cmd+Q no Dock), o servidor encerra junto.

**Linux:**

```bash
./installer/install.sh
```

Cria `~/.local/share/applications/transcriptor.desktop`. Abra pelo menu de aplicativos da sua distro. O ícone usa o SVG embarcado — se quiser que o ícone seja gerado, tenha `rsvg-convert`, `inkscape` ou `imagemagick` instalado.

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy Bypass -File installer\windows\install.ps1
# com atalho na Área de Trabalho:
powershell -ExecutionPolicy Bypass -File installer\windows\install.ps1 -Desktop
```

Cria um atalho no Menu Iniciar (e opcionalmente na Área de Trabalho) que invoca um `.vbs` wrapper — a janela do CMD nunca aparece.

**Desinstalar:**

```bash
./installer/uninstall.sh                                                 # macOS/Linux
powershell -ExecutionPolicy Bypass -File installer\windows\uninstall.ps1 # Windows
```

Remove só o atalho do sistema — o diretório do projeto fica intacto.

## Como usar

1. Abra `http://localhost:8765` (ou clique no ícone do app)
2. Arraste um vídeo/áudio na zona de upload
3. Escolha o modelo (o ponto verde mostra quais já estão baixados):
   - **Tiny / Base / Small** — rápidos, qualidade aceitável
   - **Medium** — sweet spot pra pt-br
   - **Large v3 Turbo** — qualidade próxima do Large v3, ~2x mais rápido
   - **Large v3** — qualidade máxima, ~3x mais lento
4. (Opcional) Ative **Identificar falantes** para marcar trechos com [Falante 1], [Falante 2]...
5. Clique em "Iniciar transcrição" — os segmentos aparecem ao vivo
6. Quando terminar: copie o texto, baixe `.txt` ou `.srt`

Se você escolher um modelo ainda não baixado, ele é baixado automaticamente no primeiro uso (~75 MB a 3 GB, ficam em `~/.cache/huggingface/`).

## Gerenciar modelos

A página **Configurações** (link no canto superior direito) permite:

- ver quanto disco os modelos estão ocupando
- baixar modelos antecipadamente (com barra de progresso ao vivo)
- remover modelos que você não usa mais
- baixar o modelo de diarização (pyannote) — requer `HF_TOKEN` no `.env`

## Identificação de falantes (diarização)

Requer um token do Hugging Face com aceite dos termos da pyannote:

1. Crie um token em https://huggingface.co/settings/tokens (leitura pública é o suficiente)
2. Aceite os termos em https://huggingface.co/pyannote/speaker-diarization-community-1
3. Crie um arquivo `.env` na raiz do projeto:
   ```
   HF_TOKEN=hf_seu_token_aqui
   ```
4. Reinicie o servidor

## Estrutura

```
transcriptor/
├── app/
│   ├── main.py              # FastAPI: rotas, SSE, orquestração
│   ├── transcriber.py       # Wrapper sobre faster-whisper
│   ├── diarizer.py          # Diarização via pyannote
│   ├── models.py            # Gestão de modelos (baixar/listar/remover)
│   └── static/
│       ├── index.html       # Shell + templates de view
│       ├── style.css        # Design system
│       └── js/
│           ├── main.js      # Bootstrap + roteamento
│           ├── api.js       # Wrapper de fetch / SSE
│           ├── ui.js        # Helpers (formatters, toasts)
│           ├── router.js    # SPA roteador (hash-based)
│           └── views/
│               ├── transcribe.js
│               └── settings.js
├── installer/
│   ├── install.sh           # Entry-point macOS/Linux
│   ├── uninstall.sh
│   ├── macos/               # Builder do .app bundle
│   ├── linux/               # .desktop + launcher
│   └── windows/             # PowerShell + .vbs wrapper
├── pyproject.toml
├── run.sh
└── README.md
```

## Mover para outro lugar

A pasta é totalmente self-contained. Pra mover:

```bash
cp -r transcriptor /destino/qualquer/
cd /destino/qualquer/transcriptor
./run.sh
# Se você tinha instalado como app, reinstale apontando pro novo caminho:
./installer/install.sh
```

Tudo o que ele cria (`.venv/`, `uploads/`, `outputs/`) fica dentro da pasta. Os modelos baixados ficam em `~/.cache/huggingface/` (compartilhado entre instâncias).

## Tecnologias

- **Backend:** FastAPI + Uvicorn
- **ML:** faster-whisper (CTranslate2) + pyannote.audio
- **Frontend:** HTML + Tailwind CSS (via CDN) + Vanilla JS modular (ES modules, zero build step)
- **Streaming:** Server-Sent Events (SSE) para transcrição e download ao vivo
