# syntax=docker/dockerfile:1.7
#
# Transcriptor — imagem Docker mínima para rodar a aplicação.
#
# Estratégia:
#   - Multi-stage: build tools só existem no estágio "builder" e são descartados.
#   - Torch CPU-only via índice oficial da PyTorch (evita ~2 GB de libs CUDA
#     que vêm por default nos wheels do PyPI).
#   - Runtime instala APENAS o que a aplicação realmente carrega:
#       ffmpeg       → decodificação de áudio/vídeo (faster-whisper, pyannote)
#       libgomp1     → OpenMP, exigido pelo CTranslate2 (multi-threading)
#       libsndfile1  → soundfile, transitivo do pyannote.audio
#   - Modelos do HF NÃO vão pra imagem (são pesados e versionáveis fora dela).
#     Devem ser persistidos em volume montado em /data/huggingface.
# ============================================================================

ARG PYTHON_VERSION=3.12

# ============================================================================
# Stage 1 — builder: instala dependências num venv isolado
# ============================================================================
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential cobre o cenário improvável de algum dep cair de wheel pra sdist.
# Tudo isso fica restrito a este estágio e é descartado no final.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# (1) Torch CPU-only do índice dedicado da PyTorch.
#     Cap em <3.0 deixa espaço pra patches sem permitir um major novo.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch>=2.6,<3.0" "torchaudio>=2.6,<3.0"

# (2) Demais deps (espelha pyproject.toml; se mudar lá, mude aqui também).
#     Não instalamos o pacote em si para que o COPY app/ no runtime stage
#     seja a única fonte de verdade do código (sem cópia duplicada em
#     site-packages).
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.32" \
        "python-multipart>=0.0.12" \
        "faster-whisper>=1.0" \
        "pyannote.audio>=4.0" \
        "python-dotenv>=1.0" \
        "huggingface_hub>=0.26"

# Faxina: remove caches de tests/__pycache__ que pip às vezes deixa para trás.
RUN find /opt/venv -depth \
        \( -type d \( -name __pycache__ -o -name tests -o -name test \) \
        -o -name "*.pyc" -o -name "*.pyo" \) \
        -exec rm -rf {} + 2>/dev/null || true


# ============================================================================
# Stage 2 — runtime: imagem final
# ============================================================================
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/data/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1

# Estritamente o necessário em runtime:
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgomp1 \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /var/log/apt/* \
    && find /usr/share/doc /usr/share/man -mindepth 1 -delete 2>/dev/null || true

# Usuário não-root com uid 1000 — facilita perms em bind mounts no Linux.
RUN useradd --create-home --uid 1000 --shell /bin/bash app

# Copia o venv pronto do builder
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Código da aplicação. Mantém a estrutura `app/` pra que `uvicorn app.main:app`
# resolva via CWD=/app.
COPY --chown=app:app app/ ./app/

# Diretórios persistentes (montados como volumes):
#   /app/uploads        → arquivos enviados pelo usuário
#   /app/outputs        → transcrições geradas (.txt / .srt)
#   /data/huggingface   → cache dos modelos (compartilhado entre containers)
RUN mkdir -p /app/uploads /app/outputs /data/huggingface \
    && chown -R app:app /app /data

USER app

EXPOSE 8765

# Healthcheck simples — não puxa deps adicionais; usa stdlib via python já presente.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys;\
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8765", \
     "--timeout-keep-alive", "600"]
