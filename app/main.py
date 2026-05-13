"""FastAPI app — UI + API de transcricao com streaming SSE."""

from __future__ import annotations

import json
import logging
import queue
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent
# Carrega .env da raiz do projeto (apps/transcriptor/.env)
load_dotenv(BASE_DIR.parent / ".env")

from . import diarizer as _diarizer_mod  # noqa: E402
from . import token_store  # noqa: E402

# Initialize token store *depois* do load_dotenv: assim ele faz snapshot do
# token que veio do .env e pode sobrescrever em RAM com o token que o usuario
# salvou pela UI (se houver).
token_store.initialize()

from .diarizer import (  # noqa: E402
    DiarizationUnavailable,
    SpeakerTurn,
    assign_speakers_to_segments,
    diarize,
)
from .models import (  # noqa: E402
    DIARIZATION_MODELS,
    delete_model,
    download_model,
    list_models,
    summary_line,
)
from .transcriber import transcribe_stream  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR.parent / "uploads"
OUTPUT_DIR = BASE_DIR.parent / "outputs"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_MODELS = {"tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"}


@dataclass
class Task:
    """Estado de uma transcricao em andamento."""

    task_id: str
    audio_path: Path
    model_size: str
    diarize: bool = False
    num_speakers: int | None = None
    events: queue.Queue[dict[str, Any] | None] = field(default_factory=queue.Queue)
    transcription: dict[str, Any] | None = None
    diarization: dict[str, Any] | None = None
    error: str | None = None


_TASKS: dict[str, Task] = {}
_TASKS_LOCK = threading.Lock()


def _worker(task: Task) -> None:
    """Roda em thread principal da task. Quando diarizacao esta ligada,
    ela roda em paralelo com a transcricao numa segunda thread (Camada 2).

    Layout temporal:
        [transcribe]   ===========>   (CPU/streaming, libera GIL no inference)
        [diarize bg]   ====>         (MPS no Apple Silicon, em paralelo)
                                     ^-- normalmente termina antes
        join + assign + emit diarization_done

    Como pyannote roda no MPS (GPU integrada) e o whisper na CPU, eles nao
    competem por recurso. Resultado: tempo total ~= max(transcribe, diarize),
    em vez de soma. Em arquivos longos com diarizacao ligada, ganho de
    25-40% de wall-clock.
    """
    diarize_thread: threading.Thread | None = None
    diarize_holder: dict[str, Any] = {}  # {"turns": [...]} ou {"error": str, "available": bool}

    def _run_diarize_bg() -> None:
        """Roda apenas a diarizacao (sem assignment) em background."""
        try:
            turns = diarize(task.audio_path, num_speakers=task.num_speakers)
            diarize_holder["turns"] = turns
        except DiarizationUnavailable as e:
            diarize_holder["error"] = str(e)
            diarize_holder["available"] = False
            logger.warning("Diarizacao indisponivel: %s", e)
        except Exception as e:
            diarize_holder["error"] = f"{type(e).__name__}: {e}"
            diarize_holder["available"] = True
            logger.exception("Erro inesperado na diarizacao em background")

    try:
        if task.diarize:
            diarize_thread = threading.Thread(
                target=_run_diarize_bg, name=f"diarize-{task.task_id[:8]}", daemon=True,
            )
            diarize_thread.start()
            logger.info("Diarizacao iniciada em paralelo (background)")

        for event in transcribe_stream(task.audio_path, model_size=task.model_size):
            task.events.put(event)
            if event["type"] == "done":
                task.transcription = event["result"]
            elif event["type"] == "error":
                task.error = event["message"]
                # Sem transcricao nao tem o que diarizar. A thread daemon termina
                # sozinha e libera o pipeline; nao precisamos abortar (pyannote
                # nao tem cancel API). Logamos so pra rastreio.
                if diarize_thread is not None and diarize_thread.is_alive():
                    logger.info("Transcricao falhou; diarizacao em bg sera abandonada")
                return

        if task.diarize and task.transcription is not None and diarize_thread is not None:
            # Aviso pro frontend que estamos finalizando a diarizacao. Quando ela
            # ja terminou em background (caso comum), a espera abaixo e instantanea.
            task.events.put({"type": "diarizing"})
            diarize_thread.join()

            if "turns" in diarize_holder:
                turns: list[SpeakerTurn] = diarize_holder["turns"]
                assignments = assign_speakers_to_segments(
                    task.transcription["segments"], turns,
                )
                unique_speakers = sorted({s for s in assignments.values()})
                event = {
                    "type": "diarization_done",
                    "speakers": unique_speakers,
                    "assignments": assignments,
                    "turns": [
                        {"start": t.start, "end": t.end, "speaker": t.speaker}
                        for t in turns
                    ],
                }
                task.diarization = event
                task.events.put(event)
            else:
                msg = diarize_holder.get("error", "Erro desconhecido na diarizacao")
                task.events.put({"type": "diarization_error", "message": msg})
    finally:
        task.events.put(None)


app = FastAPI(title="Transcriptor")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/config")
def config() -> dict[str, Any]:
    """Indica ao frontend quais features estao disponiveis.

    ``diarization_available`` so reflete "tem token configurado". A validade
    real (token aceito + termos aceitos por repo) e checada em /api/hf/status
    e /api/hf/access — chamadas mais pesadas, feitas sob demanda.
    """
    return {
        "diarization_available": bool(token_store.get_token()),
        "models": sorted(ALLOWED_MODELS),
        "version": "0.2.0",
    }


# ============================================================================
# Hugging Face token: status, set, delete, model access check
# ============================================================================


class _TokenPayload(BaseModel):
    """Body de POST /api/hf/token."""

    token: str


# Timeout curto: a UI espera de forma sincrona, entao loading "eterno" e
# inaceitavel. 8s cobre conexoes lentas e ainda fica abaixo do limite onde o
# usuario comeca a se perguntar se a app travou.
_HF_API_BASE = "https://huggingface.co"
_HF_API_TIMEOUT = 8.0


def _hf_whoami(token: str) -> dict[str, Any]:
    """Chama whoami diretamente via requests com timeout.

    Nao usamos ``HfApi.whoami()`` porque ele nao expoe ``timeout`` e fica
    pendurado indefinidamente quando o HF esta lento/offline.
    """
    try:
        resp = requests.get(
            f"{_HF_API_BASE}/api/whoami-v2",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_HF_API_TIMEOUT,
        )
    except requests.Timeout:
        raise HTTPException(
            504,
            f"Hugging Face nao respondeu em {_HF_API_TIMEOUT:.0f}s. "
            "Verifique sua conexao com a internet e tente novamente.",
        ) from None
    except requests.RequestException as e:
        raise HTTPException(
            502,
            f"Falha conectando ao Hugging Face: {type(e).__name__}.",
        ) from None
    if resp.status_code in (401, 403):
        raise HTTPException(
            401,
            "Token rejeitado pelo Hugging Face. Verifique se copiou o token "
            "completo e se ele ainda esta ativo em huggingface.co/settings/tokens.",
        ) from None
    if not resp.ok:
        raise HTTPException(
            resp.status_code,
            f"Hugging Face retornou HTTP {resp.status_code}.",
        ) from None
    try:
        return resp.json()
    except ValueError:
        raise HTTPException(500, "Resposta invalida do Hugging Face.") from None


def _hf_check_repo_access(token: str, repo_id: str) -> str | None:
    """Retorna a ``reason`` se o repo nao for acessivel; ``None`` se for.

    Chamamos diretamente o endpoint REST do HF com timeout para evitar que a
    pagina de configuracoes trave esperando.
    """
    try:
        resp = requests.get(
            f"{_HF_API_BASE}/api/models/{repo_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_HF_API_TIMEOUT,
        )
    except requests.Timeout:
        return "timeout"
    except requests.RequestException:
        return "network_error"
    if resp.ok:
        return None
    if resp.status_code == 401:
        return "unauthorized"
    # HF retorna 403 (gated) ou 404 (existente mas oculto para esse token).
    # Tratamos ambos como "gated" — a acao do usuario e a mesma: aceitar termos.
    if resp.status_code in (403, 404):
        return "gated"
    logger.warning("HTTP %s checando acesso a %s", resp.status_code, repo_id)
    return "http_error"


@app.get("/api/hf/status")
def hf_status() -> dict[str, Any]:
    """Estado atual do token: configurado? valido? quem? de onde vem?

    Faz uma chamada whoami() no HF — pode levar 200-800ms. Nao cacheamos:
    o usuario costuma chamar este endpoint depois de mudar algo (salvar token,
    aceitar termos), e queremos refletir o estado atual.
    """
    token = token_store.get_token()
    source = token_store.get_source()
    payload: dict[str, Any] = {
        "configured": bool(token),
        "valid": None,
        "source": source,
        "username": None,
        "masked": token_store.mask(token) if token else None,
        "error": None,
        "config_path": str(token_store.CONFIG_FILE),
    }
    if not token:
        return payload
    try:
        info = _hf_whoami(token)
        payload["valid"] = True
        payload["username"] = info.get("name") or info.get("fullname")
    except HTTPException as e:
        # whoami falhou -> token presente mas invalido. Nao propagamos o erro
        # como HTTP; retornamos no payload pra UI poder mostrar o estado real.
        payload["valid"] = False
        payload["error"] = e.detail
    return payload


@app.post("/api/hf/token")
def hf_set_token(payload: _TokenPayload) -> dict[str, Any]:
    """Recebe um token, valida via whoami(), persiste e atualiza o ambiente."""
    raw = (payload.token or "").strip()
    if not raw:
        raise HTTPException(400, "Campo 'token' e obrigatorio.")
    if not raw.startswith("hf_"):
        raise HTTPException(400, "Token Hugging Face deve comecar com 'hf_'.")
    if any(c.isspace() for c in raw):
        raise HTTPException(400, "Token nao pode conter espacos ou quebras de linha.")

    info = _hf_whoami(raw)

    try:
        token_store.set_token(raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    # Reset do pipeline pyannote — sem isso a proxima diarizacao usaria o
    # token antigo carregado em RAM.
    _diarizer_mod.reset_pipeline()

    return {
        "configured": True,
        "valid": True,
        "source": token_store.get_source(),
        "username": info.get("name") or info.get("fullname"),
        "masked": token_store.mask(raw),
    }


@app.delete("/api/hf/token")
def hf_delete_token() -> dict[str, Any]:
    """Remove o token salvo. Se havia um token no .env, voltamos pra ele."""
    token_store.delete_token()
    _diarizer_mod.reset_pipeline()
    new_token = token_store.get_token()
    return {
        "removed": True,
        "configured": bool(new_token),
        "source": token_store.get_source(),
        "masked": token_store.mask(new_token) if new_token else None,
    }


@app.get("/api/hf/access")
def hf_access() -> dict[str, Any]:
    """Verifica acesso aos repos gated necessarios para o app.

    Esses sao os repos do pyannote (atualmente apenas
    pyannote/speaker-diarization-community-1). Para cada um:
      - 200 -> usuario aceitou os termos
      - 403/404 -> termos nao aceitos (tratado como "gated")
      - 401 -> token invalido
      - timeout/erro -> reportado pra UI conseguir mostrar feedback util
    """
    token = token_store.get_token()
    has_token = bool(token)

    items: list[dict[str, Any]] = []
    for spec in DIARIZATION_MODELS:
        item: dict[str, Any] = {
            "key": spec.key,
            "repo_id": spec.repo_id,
            "label": spec.label,
            "url": f"https://huggingface.co/{spec.repo_id}",
            "accessible": False,
            "reason": None,
        }
        if not has_token:
            item["reason"] = "no_token"
        else:
            reason = _hf_check_repo_access(token, spec.repo_id)
            if reason is None:
                item["accessible"] = True
            else:
                item["reason"] = reason
        items.append(item)
    return {"models": items, "has_token": has_token}


@app.post("/api/transcribe")
async def start_transcribe(
    file: UploadFile = File(...),
    model: str = Form("medium"),
    diarize: bool = Form(False),
    num_speakers: int | None = Form(None),
) -> dict[str, Any]:
    if model not in ALLOWED_MODELS:
        raise HTTPException(400, f"Modelo invalido. Use um de: {sorted(ALLOWED_MODELS)}")
    if not file.filename:
        raise HTTPException(400, "Arquivo sem nome.")
    if num_speakers is not None and num_speakers < 1:
        num_speakers = None  # 0 ou negativo = auto-detect
    if diarize and not token_store.get_token():
        # Falha cedo (antes de aceitar o upload). Sem isso o erro so apareceria
        # no meio do streaming, depois do upload completo — UX ruim.
        raise HTTPException(
            400,
            "Diarizacao requer um token Hugging Face. Configure-o em "
            "Configuracoes (link no menu superior).",
        )

    task_id = uuid.uuid4().hex
    safe_name = Path(file.filename).name
    dest = UPLOAD_DIR / f"{task_id}__{safe_name}"

    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out, length=1024 * 1024)
    logger.info(
        "Upload: %s (%.1f MB) -> task %s | diarize=%s num_speakers=%s",
        safe_name,
        dest.stat().st_size / 1e6,
        task_id,
        diarize,
        num_speakers,
    )

    task = Task(
        task_id=task_id,
        audio_path=dest,
        model_size=model,
        diarize=diarize,
        num_speakers=num_speakers,
    )
    with _TASKS_LOCK:
        _TASKS[task_id] = task

    threading.Thread(target=_worker, args=(task,), daemon=True).start()

    return {
        "task_id": task_id,
        "filename": safe_name,
        "model": model,
        "diarize": diarize,
    }


def _sse_format(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/api/stream/{task_id}")
def stream(task_id: str) -> StreamingResponse:
    task = _TASKS.get(task_id)
    if task is None:
        raise HTTPException(404, "Task nao encontrada.")

    def gen():
        while True:
            event = task.events.get()
            if event is None:
                break
            yield _sse_format(event)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _build_speaker_text(task: Task, labels: dict[str, str]) -> str:
    """Texto plano com prefixo de falante. labels mapeia SPEAKER_00 -> 'Joao'."""
    if task.transcription is None:
        return ""
    assignments = task.diarization["assignments"] if task.diarization else {}
    lines: list[str] = []
    last_speaker: str | None = None
    for seg in task.transcription["segments"]:
        speaker_raw = assignments.get(str(seg["index"])) or assignments.get(seg["index"])
        speaker = labels.get(speaker_raw, speaker_raw) if speaker_raw else None
        if speaker and speaker != last_speaker:
            lines.append(f"\n[{speaker}]")
            last_speaker = speaker
        lines.append(seg["text"])
    return "\n".join(lines).strip()


def _build_speaker_srt(task: Task, labels: dict[str, str]) -> str:
    if task.transcription is None:
        return ""
    assignments = task.diarization["assignments"] if task.diarization else {}
    lines: list[str] = []
    for seg in task.transcription["segments"]:
        speaker_raw = assignments.get(str(seg["index"])) or assignments.get(seg["index"])
        speaker = labels.get(speaker_raw, speaker_raw) if speaker_raw else None
        prefix = f"[{speaker}] " if speaker else ""
        lines.append(str(seg["index"]))
        lines.append(f"{seg['start_ts']} --> {seg['end_ts']}")
        lines.append(f"{prefix}{seg['text']}")
        lines.append("")
    return "\n".join(lines)


@app.get("/api/download/{task_id}/{fmt}")
def download(task_id: str, fmt: str, labels: str = "") -> Any:
    """Download em txt ou srt. `labels` e um JSON opcional com renomes de falante."""
    task = _TASKS.get(task_id)
    if task is None or task.transcription is None:
        raise HTTPException(404, "Resultado nao disponivel.")

    label_map: dict[str, str] = {}
    if labels:
        try:
            label_map = json.loads(labels)
        except json.JSONDecodeError:
            raise HTTPException(400, "Parametro 'labels' deve ser JSON valido.") from None

    base_name = task.audio_path.stem.split("__", 1)[-1]
    if fmt == "txt":
        content = _build_speaker_text(task, label_map) if task.diarization else task.transcription["full_text"]
        media = "text/plain; charset=utf-8"
        ext = "txt"
    elif fmt == "srt":
        content = _build_speaker_srt(task, label_map) if task.diarization else task.transcription["srt"]
        media = "application/x-subrip; charset=utf-8"
        ext = "srt"
    else:
        raise HTTPException(400, "Formato invalido. Use 'txt' ou 'srt'.")

    out_path = OUTPUT_DIR / f"{base_name}.{ext}"
    out_path.write_text(content, encoding="utf-8")

    return FileResponse(out_path, media_type=media, filename=out_path.name)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ============================================================================
# Gestão de modelos (download / listar / remover)
# ============================================================================

# Locks de download por chave de modelo — impede que duas requests simultâneas
# tentem baixar o mesmo modelo (geraria conflito no cache do HF).
_DOWNLOAD_LOCKS: dict[str, threading.Lock] = {}
_DOWNLOAD_LOCKS_GUARD = threading.Lock()


def _get_download_lock(key: str) -> threading.Lock:
    with _DOWNLOAD_LOCKS_GUARD:
        if key not in _DOWNLOAD_LOCKS:
            _DOWNLOAD_LOCKS[key] = threading.Lock()
        return _DOWNLOAD_LOCKS[key]


@app.get("/api/models")
def models_list() -> dict[str, Any]:
    """Retorna o catálogo completo de modelos com status de cada um."""
    return list_models()


@app.get("/api/models/summary")
def models_summary() -> dict[str, str]:
    return {"summary": summary_line()}


@app.get("/api/models/{key}/download")
def models_download(key: str) -> StreamingResponse:
    """Baixa um modelo emitindo progresso via SSE.

    GET (não POST) porque EventSource só suporta GET. Idempotente do ponto de
    vista do cache: se já está em disco, snapshot_download é praticamente um
    no-op (~50ms verificando hash).
    """
    lock = _get_download_lock(key)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, f"Download de '{key}' já em andamento.")

    def gen():
        try:
            for event in download_model(key):
                yield _sse_format(event)
        finally:
            lock.release()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.delete("/api/models/{key}")
def models_delete(key: str) -> dict[str, Any]:
    """Remove o cache do modelo do disco (e da RAM se aplicável)."""
    try:
        return delete_model(key)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
