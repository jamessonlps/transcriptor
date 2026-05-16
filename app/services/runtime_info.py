"""Inspeção de runtime: que device cada engine vai usar.

Pra a UI mostrar "GPU: NVIDIA RTX 4060" ou "CPU (8 threads)" sem ter que
duplicar a lógica de detecção. Lê o estado *prospectivo* — o que vai
acontecer na próxima inferência, com base em env + hardware disponível.

A transcrição (faster-whisper / CTranslate2) só sabe cpu/cuda.
A diarização (pyannote / torch) sabe cpu/cuda/mps.
"""

from __future__ import annotations

import logging
import platform
from typing import Any

from app.transcriber import _detect_threads, detect_device

logger = logging.getLogger(__name__)


def _detect_diarization_device() -> dict[str, Any]:
    """Mesma heurística do diarizer.get_pipeline, sem carregar o pipeline.

    O pipeline real é carregado lazy quando o usuário pede diarização
    pela primeira vez — aqui só inspecionamos torch pra reportar.
    """
    try:
        import torch
    except Exception:
        return {"device": "cpu", "gpu_name": None, "reason": "torch indisponível"}

    try:
        if torch.backends.mps.is_available():
            return {
                "device": "mps",
                "gpu_name": "Apple Silicon GPU",
                "reason": "MPS detectada (Apple Silicon)",
            }
    except Exception:
        pass

    try:
        if torch.cuda.is_available() and torch.cuda.device_count() > 0:
            try:
                name = torch.cuda.get_device_name(0)
            except Exception:
                name = "CUDA device"
            return {"device": "cuda", "gpu_name": name, "reason": "CUDA detectada"}
    except Exception:
        pass

    return {"device": "cpu", "gpu_name": None, "reason": "sem GPU disponível"}


def get_runtime_info() -> dict[str, Any]:
    """Snapshot do runtime, consumido por ``/api/runtime`` e pela UI."""
    dev = detect_device()
    transcription = {
        "device": dev.device,
        "compute_type": dev.compute_type,
        "gpu_name": dev.gpu_name,
        "reason": dev.reason,
        "cpu_threads": _detect_threads() if dev.device == "cpu" else None,
    }
    diarization = _detect_diarization_device()

    return {
        "platform": {
            "system": platform.system(),  # "Darwin" | "Linux" | "Windows"
            "machine": platform.machine(),  # "arm64" | "x86_64" | ...
        },
        "transcription": transcription,
        "diarization": diarization,
    }
