# Transcriptor

> Web app local para transcrição de vídeos e áudios em pt-br. Tudo roda no
> seu computador — nada vai pra nuvem. Stack: FastAPI + faster-whisper +
> pyannote, com streaming SSE e identificação de falantes em paralelo na GPU.

[![CI](https://github.com/jamessonfelipe/transcriptor/actions/workflows/ci.yml/badge.svg)](https://github.com/jamessonfelipe/transcriptor/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![100% local](https://img.shields.io/badge/100%25-local-success)](#)
[![Docker ready](https://img.shields.io/badge/Docker-ready-2496ED)](./Dockerfile)

🇬🇧 English version: [`README-en.md`](./README-en.md)

<!-- TODO: substituir por GIF/screenshot do fluxo principal -->
<!-- ![Transcriptor — drag-drop → live transcript → diarisation](docs/img/demo.gif) -->

## O que é

- 🎙️ Transcrição de qualquer áudio ou vídeo (mp4, mov, mp3, wav, m4a, webm…) em
    português-brasileiro usando `faster-whisper` (CTranslate2, int8 na CPU).
- 👥 Identificação de falantes (diarização) opcional via `pyannote.audio` 4,
    rodando **em paralelo** com a transcrição (MPS no Apple Silicon, CUDA no
    NVIDIA, CPU se nada estiver disponível).
- 🌊 Streaming SSE — os segmentos aparecem na tela conforme são produzidos, não
    no fim.
- 💾 Gestão de modelos pela UI: ver disco usado, baixar com barra de progresso
    ao vivo, deletar.
- 🔐 Token Hugging Face gerenciado pela própria app (persiste em
    `~/.cache/transcriptor/config.json` com modo `0600`, validação por whoami,
    setup wizard pra modelos gated).
- 🐳 Roda nativamente, como app instalado (.app/.desktop/.lnk), ou em Docker
    (~1.6 GB CPU-only).

## Por que vale a leitura técnica

Este projeto é tão sobre **engenharia** quanto sobre o produto. Os pontos
mais interessantes pra revisar:

- **`app/services/transcription_worker.py`** — paralelismo CPU/GPU entre os
    dois modelos, com tratamento explícito dos modos de falha. ~25-40% mais
    rápido em arquivos longos com diarização (vs sequencial). Veja
    [ADR-0004](./docs/adr/0004-parallel-transcribe-diarise.md).
- **`app/services/task_manager.py`** — registry em memória com **TTL +
    capacidade**. Substitui um `dict` global que não evictava nada e vazava
    RAM em sessões longas. Coberto por
    [`tests/test_task_manager.py`](./tests/test_task_manager.py).
- **`app/transcriber.py`** — cache single-slot com eviction explícita, pra
    permitir trocar de `medium` (1.5 GB) pra `large-v3` (3 GB) em máquinas com
    8 GB de RAM. [ADR-0002](./docs/adr/0002-whisper-model-eviction.md).
- **`app/static/js/views/transcribe.js`** — vanilla JS sem framework, mas
    com separação clara entre state machine (`class State extends EventTarget`)
    e camada de render (funções puras `render*`). Vanilla-JS flavor de Flux,
    sem dependências.
- **SSE em vez de WebSocket** — racional em
    [ADR-0001](./docs/adr/0001-sse-over-websockets.md).
- **Zero build step no frontend** — racional em
    [ADR-0003](./docs/adr/0003-no-build-step.md).

Documentação técnica completa em [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md).

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

O script é idempotente: cria o virtualenv, instala dependências (só na
primeira vez) e sobe o servidor em `http://localhost:8765`.

Para usar outra porta:

```bash
PORT=9000 ./run.sh
```

### Modo 2 — Instalar como app no sistema

Cria um ícone no Launchpad/Menu Iniciar/Aplicações que sobe o servidor sem
terminal visível e abre o navegador automaticamente.

**macOS:**

```bash
./installer/install.sh
```

Cria `~/Applications/Transcriptor.app`. Abra pelo Spotlight
(`Cmd+Space → Transcriptor`) ou pelo Launchpad. Quando você "sair" (Cmd+Q no
Dock), o servidor encerra junto.

**Linux:**

```bash
./installer/install.sh
```

Cria `~/.local/share/applications/transcriptor.desktop`. Abra pelo menu de
aplicativos da sua distro. O ícone usa o SVG embarcado — se quiser que o
ícone seja gerado, tenha `rsvg-convert`, `inkscape` ou `imagemagick`
instalado.

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy Bypass -File installer\windows\install.ps1
# com atalho na Área de Trabalho:
powershell -ExecutionPolicy Bypass -File installer\windows\install.ps1 -Desktop
```

Cria um atalho no Menu Iniciar (e opcionalmente na Área de Trabalho) que
invoca um `.vbs` wrapper — a janela do CMD nunca aparece.

**Desinstalar:**

```bash
./installer/uninstall.sh                                                 # macOS/Linux
powershell -ExecutionPolicy Bypass -File installer\windows\uninstall.ps1 # Windows
```

Remove só o atalho do sistema — o diretório do projeto fica intacto.

### Modo 3 — Docker

Sem precisar de Python ou ffmpeg no host — só Docker. A imagem é multi-stage,
**torch CPU-only** (sem ~2 GB de libs CUDA) e fica em ~1.6 GB.

```bash
docker compose up -d --build
# acesse http://localhost:8765
```

Volumes nomeados (`hf-cache`, `uploads`, `outputs`) persistem entre rebuilds —
modelos baixados pela UI sobrevivem a `docker compose down`/`up`. O `.env` da
raiz (se existir) é lido automaticamente pelo compose pra injetar `HF_TOKEN`
no container, sem ir pra dentro da imagem.

```bash
docker compose logs -f   # acompanhar logs
docker compose down      # parar (mantém volumes)
docker compose down -v   # parar + apagar volumes (incluindo modelos baixados)
```

Pra trocar a porta exposta no host: `PORT=9000 docker compose up -d`.

## Como usar

1. Abra `http://localhost:8765` (ou clique no ícone do app)
2. Arraste um vídeo/áudio na zona de upload
3. Escolha o modelo (o ponto verde mostra quais já estão baixados):
     - **Tiny / Base / Small** — rápidos, qualidade aceitável
     - **Medium** — sweet spot pra pt-br
     - **Large v3 Turbo** — qualidade próxima do Large v3, ~2x mais rápido
     - **Large v3** — qualidade máxima, ~3x mais lento
4. (Opcional) Ative **Identificar falantes** para marcar trechos com
     [Falante 1], [Falante 2]...
5. Clique em "Iniciar transcrição" — os segmentos aparecem ao vivo
6. Quando terminar: copie o texto, baixe `.txt` ou `.srt`

Se você escolher um modelo ainda não baixado, ele é baixado automaticamente no
primeiro uso (~75 MB a 3 GB, ficam em `~/.cache/huggingface/`).

## Identificação de falantes (diarização)

A página **Configurações** tem um wizard que guia o setup. Resumo:

1. Crie uma conta gratuita em https://huggingface.co/join
2. Gere um token de leitura em https://huggingface.co/settings/tokens
3. Aceite os termos em
     https://huggingface.co/pyannote/speaker-diarization-community-1
     usando a **mesma conta** do token
4. Cole o token no campo de **Configurações → Hugging Face → Salvar token**

O token é salvo em `~/.cache/transcriptor/config.json` (permissão `0600`) e
tem efeito imediato — sem reiniciar o servidor.

Alternativa: setar `HF_TOKEN=hf_xxx` num arquivo `.env`. O token salvo pela
UI tem prioridade; remover pela UI volta automaticamente pro `.env`.

## Desenvolvimento

```bash
git clone https://github.com/jamessonfelipe/transcriptor.git
cd transcriptor
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                      # 90+ tests, <1s
ruff check . && mypy app    # lint + typecheck
```

Detalhes em [`CONTRIBUTING.md`](./CONTRIBUTING.md).

## Estrutura

```
transcriptor/
├── app/
│   ├── main.py                    # FastAPI factory (mounts static + routers)
│   ├── routers/                   # HTTP layer (health, hf, transcribe, models)
│   ├── services/                  # Orchestration (task_manager, hf_client, ...)
│   ├── domain/                    # Pure data structures (Task)
│   ├── transcriber.py             # faster-whisper wrapper
│   ├── diarizer.py                # pyannote 4 wrapper
│   ├── models.py                  # Model catalog + download/delete
│   ├── token_store.py             # HF token persistence
│   └── static/                    # SPA (no build step)
│       ├── index.html
│       ├── css/                   # base, layout, components
│       └── js/                    # main, router, api, ui, components, views
├── tests/                         # pytest — 90+ tests
├── docs/
│   ├── ARCHITECTURE.md            # System design
│   └── adr/                       # Architecture Decision Records
├── installer/                     # macOS .app, Linux .desktop, Windows .vbs
├── .github/workflows/ci.yml       # Ruff + mypy + pytest + docker build
├── Dockerfile                     # Multi-stage, CPU-only Torch
├── docker-compose.yml             # Volumes + healthcheck
├── pyproject.toml                 # Project metadata + dev extras
├── ruff.toml                      # Lint + format config
└── mypy.ini                       # Type-check config
```

## Mover para outro lugar

A pasta é totalmente self-contained:

```bash
cp -r transcriptor /destino/qualquer/
cd /destino/qualquer/transcriptor
./run.sh
# Se você tinha instalado como app, reinstale apontando pro novo caminho:
./installer/install.sh
```

Tudo o que ele cria (`.venv/`, `uploads/`, `outputs/`) fica dentro da pasta.
Os modelos baixados ficam em `~/.cache/huggingface/` (compartilhado entre
instâncias).

## Tecnologias

- **Backend:** FastAPI + Uvicorn, threading-based task management.
- **ML:** `faster-whisper` (CTranslate2) + `pyannote.audio` 4 (torch
    CPU-only no Docker; MPS/CUDA detectado no host).
- **Frontend:** HTML + Tailwind CDN JIT + Vanilla JS modular (ES modules,
    zero build step).
- **Streaming:** Server-Sent Events para transcrição e download ao vivo.
- **Distribuição:** `run.sh` (dev) · `.app`/`.desktop`/atalho do Windows
    (instalador nativo) · Docker multi-stage.
- **Qualidade:** Ruff + mypy + pytest (90+ tests), CI no GitHub Actions.

## Licença

[MIT](./LICENSE).
