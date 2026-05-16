"""Transcription endpoints: upload, SSE stream, download.

The actual transcription work lives in ``services.transcription_worker``;
this router just hands off to a worker thread and wires the HTTP shape.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app import token_store
from app.domain.task import Task
from app.routers.health import ALLOWED_MODELS
from app.services import output_formatter, transcription_worker
from app.services.sse import STREAM_HEADERS, format_event
from app.services.task_manager import TaskManager

logger = logging.getLogger(__name__)

# Module-global state so we have *one* registry across the app — FastAPI's
# Dependency Injection isn't worth the ceremony here, since these are
# process-singleton anyway.
tasks = TaskManager()


def _resolve_dirs() -> tuple[Path, Path]:
    """Late binding for upload/output paths.

    The test suite monkeypatches ``app.main.UPLOAD_DIR`` / ``OUTPUT_DIR``
    after import. Resolving lazily on each call keeps tests honest without
    forcing them to also patch this router.
    """
    from app import main

    return main.UPLOAD_DIR, main.OUTPUT_DIR


router = APIRouter(prefix="/api", tags=["transcribe"])


@router.post("/transcribe")
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
        num_speakers = None  # 0 or negative = auto-detect
    if diarize and not token_store.get_token():
        # Fail before accepting the upload. Otherwise the error only shows
        # mid-stream after the upload is complete — bad UX.
        raise HTTPException(
            400,
            "Diarizacao requer um token Hugging Face. Configure-o em "
            "Configuracoes (link no menu superior).",
        )

    task_id = uuid.uuid4().hex
    safe_name = Path(file.filename).name
    upload_dir, _ = _resolve_dirs()
    dest = upload_dir / f"{task_id}__{safe_name}"

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
    tasks.add(task)

    threading.Thread(
        target=transcription_worker.run,
        args=(task,),
        daemon=True,
    ).start()

    return {
        "task_id": task_id,
        "filename": safe_name,
        "model": model,
        "diarize": diarize,
    }


@router.get("/stream/{task_id}")
def stream(task_id: str) -> StreamingResponse:
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(404, "Task nao encontrada.")

    def gen():
        while True:
            event = task.events.get()
            if event is None:
                break
            yield format_event(event)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


@router.get("/download/{task_id}/{fmt}")
def download(task_id: str, fmt: str, labels: str = "") -> Any:
    """Download TXT or SRT. ``labels`` is an optional JSON of speaker renames."""
    task = tasks.get(task_id)
    if task is None or task.transcription is None:
        raise HTTPException(404, "Resultado nao disponivel.")

    label_map: dict[str, str] = {}
    if labels:
        try:
            label_map = json.loads(labels)
        except json.JSONDecodeError:
            raise HTTPException(400, "Parametro 'labels' deve ser JSON valido.") from None

    base_name = task.audio_path.stem.split("__", 1)[-1]
    _, output_dir = _resolve_dirs()
    if fmt == "txt":
        content = (
            output_formatter.build_text(task, label_map)
            if task.diarization
            else task.transcription["full_text"]
        )
        media = "text/plain; charset=utf-8"
        ext = "txt"
    elif fmt == "srt":
        content = (
            output_formatter.build_srt(task, label_map)
            if task.diarization
            else task.transcription["srt"]
        )
        media = "application/x-subrip; charset=utf-8"
        ext = "srt"
    else:
        raise HTTPException(400, "Formato invalido. Use 'txt' ou 'srt'.")

    out_path = output_dir / f"{base_name}.{ext}"
    out_path.write_text(content, encoding="utf-8")

    return FileResponse(out_path, media_type=media, filename=out_path.name)
