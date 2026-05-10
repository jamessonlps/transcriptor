# Transcriptor

Web app local para transcrição de vídeos/áudios em pt-br. Roda 100% no seu computador (nada sai pra nuvem) usando [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## Pré-requisitos

- Python 3.10+
- ffmpeg
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: baixe em https://ffmpeg.org/download.html

## Como rodar

```bash
./run.sh
```

O script é idempotente: cria o virtualenv, instala dependências (só na primeira vez) e sobe o servidor em `http://localhost:8765`.

Para usar outra porta:

```bash
PORT=9000 ./run.sh
```

## Como usar

1. Abra `http://localhost:8765`
2. Arraste um vídeo/áudio na zona de upload
3. Escolha o modelo:
   - **Tiny / Base / Small** — rápidos, qualidade aceitável
   - **Medium** (padrão) — sweet spot pra pt-br
   - **Large v3** — qualidade máxima, ~3x mais lento
4. Clique em "Iniciar transcrição" — os segmentos aparecem ao vivo
5. Quando terminar: copie o texto, baixe `.txt` ou `.srt`

Na primeira vez que você usar um modelo, ele é baixado (~75MB a 3GB dependendo do tamanho) em `~/.cache/huggingface/`. Depois fica em cache.

## Estrutura

```
apps/transcriptor/
├── app/
│   ├── main.py           # FastAPI: rotas, SSE, orquestração
│   ├── transcriber.py    # Wrapper sobre faster-whisper (gerador de eventos)
│   └── static/
│       ├── index.html    # UI
│       ├── app.js        # Lógica frontend (vanilla JS)
│       └── style.css     # Estilos custom (Tailwind via CDN)
├── pyproject.toml        # Dependências Python
├── run.sh                # Setup + start em um comando
└── README.md
```

## Mover para outro lugar

A pasta é totalmente self-contained. Pra mover:

```bash
cp -r apps/transcriptor /destino/qualquer/
cd /destino/qualquer/transcriptor
./run.sh
```

Tudo o que ele cria (`.venv/`, `uploads/`, `outputs/`) fica dentro da pasta.

## Tecnologias

- **Backend:** FastAPI + Uvicorn
- **ML:** faster-whisper (CTranslate2)
- **Frontend:** HTML + Tailwind CSS (via CDN) + Vanilla JS
- **Streaming:** Server-Sent Events (SSE) para transcrição ao vivo
