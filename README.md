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

### Modo 3 — Docker

Sem precisar de Python ou ffmpeg no host — só Docker. A imagem é multi-stage, **torch CPU-only** (sem ~2 GB de libs CUDA) e fica em ~1.6 GB.

```bash
docker compose up -d --build
# acesse http://localhost:8765
```

Volumes nomeados (`hf-cache`, `uploads`, `outputs`) persistem entre rebuilds — modelos baixados pela UI sobrevivem a `docker compose down`/`up`. O `.env` da raiz (se existir) é lido automaticamente pelo compose pra injetar `HF_TOKEN` no container, sem ir pra dentro da imagem.

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
4. (Opcional) Ative **Identificar falantes** para marcar trechos com [Falante 1], [Falante 2]...
5. Clique em "Iniciar transcrição" — os segmentos aparecem ao vivo
6. Quando terminar: copie o texto, baixe `.txt` ou `.srt`

Se você escolher um modelo ainda não baixado, ele é baixado automaticamente no primeiro uso (~75 MB a 3 GB, ficam em `~/.cache/huggingface/`).

## Gerenciar modelos

A página **Configurações** (link no canto superior direito) permite:

- ver quanto disco os modelos estão ocupando
- baixar modelos antecipadamente (com barra de progresso ao vivo)
- remover modelos que você não usa mais
- configurar o token Hugging Face (com wizard de setup) e ver o status de acesso a cada modelo gated
- baixar o modelo de diarização (pyannote) após o token estar configurado

## Identificação de falantes (diarização)

A página **Configurações** tem um wizard que guia o setup. Resumo do que ele cobre:

1. Crie uma conta gratuita em https://huggingface.co/join (se ainda não tem)
2. Gere um token de leitura em https://huggingface.co/settings/tokens (tipo "Read")
3. Aceite os termos em https://huggingface.co/pyannote/speaker-diarization-community-1
   usando a **mesma conta** do token
4. Cole o token no campo de **Configurações → Hugging Face → Salvar token**

O token é salvo em `~/.cache/transcriptor/config.json` (permissão `0600`, só seu usuário lê) e tem efeito imediato — sem reiniciar o servidor.

Alternativa: setar `HF_TOKEN=hf_xxx` num arquivo `.env` na raiz do projeto (modo legado). Vale lembrar que o token salvo pela UI tem prioridade sobre o `.env` — se você remover o token pela UI e tiver um `.env`, voltamos automaticamente pro valor do `.env`.

A UI distingue três falhas comuns e mostra orientação específica em cada uma:
- **Token ausente** → wizard aberto com instruções passo a passo
- **Token inválido/revogado** → pede pra gerar um novo
- **Token válido, mas conta não aceitou os termos do modelo** → link direto pra página de termos com o botão "Aceitar termos"

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
├── Dockerfile               # Multi-stage, torch CPU-only, runtime mínimo
├── docker-compose.yml       # Volumes + env_file + healthcheck
├── .dockerignore
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
- **ML:** faster-whisper (CTranslate2) + pyannote.audio (torch CPU-only)
- **Frontend:** HTML + Tailwind CSS (bundle local) + Vanilla JS modular (ES modules, zero build step)
- **Streaming:** Server-Sent Events (SSE) para transcrição e download ao vivo
- **Distribuição:** `run.sh` (dev) · `.app`/`.desktop`/atalho do Windows (instalador nativo) · Docker (multi-stage, ~1.6 GB)
