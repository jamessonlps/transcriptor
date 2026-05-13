"""Gerenciamento de modelos baixados localmente.

Esta camada permite:
    - listar quais modelos (Whisper + pyannote) já estão em cache,
    - baixar um modelo sob demanda emitindo eventos de progresso,
    - remover um modelo do cache (evitando ficar com 3GB órfãos no disco).

Os modelos vivem em ``~/.cache/huggingface/hub/models--<org>--<repo>/``.
Cada um tem subdirs ``snapshots/<hash>/`` (symlinks pra blobs) e ``blobs/``
(arquivos reais). Pra calcular tamanho real, resolvemos os symlinks com du.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, scan_cache_dir, snapshot_download
from huggingface_hub.utils import (
    GatedRepoError,
    HfHubHTTPError,
    RepositoryNotFoundError,
)

from . import transcriber

logger = logging.getLogger(__name__)


# ---------- Catálogo ----------

@dataclass(frozen=True)
class ModelSpec:
    """Especificação estática de um modelo (independente de estar baixado ou não)."""

    key: str
    label: str
    repo_id: str
    kind: str          # "whisper" | "diarization"
    size_mb: int       # tamanho aproximado em disco
    speed_label: str   # "~10x", "~2x"... (relativo a tempo real de áudio)
    description: str
    tag: str | None = None  # "novo", "máx. precisão", etc.


WHISPER_MODELS: list[ModelSpec] = [
    ModelSpec("tiny", "Tiny", "Systran/faster-whisper-tiny", "whisper",
              75, "~10x", "Mais rápido, qualidade limitada — útil pra testes."),
    ModelSpec("base", "Base", "Systran/faster-whisper-base", "whisper",
              140, "~7x", "Compromisso pra notas rápidas de fala clara."),
    ModelSpec("small", "Small", "Systran/faster-whisper-small", "whisper",
              460, "~4x", "Boa qualidade pra fala limpa, ainda rápido."),
    ModelSpec("medium", "Medium", "Systran/faster-whisper-medium", "whisper",
              1500, "~2x", "Sweet spot pra pt-br — recomendado pra reuniões."),
    # mobiuslabsgmbh republica o turbo em formato CT2 (faster-whisper). O repo
    # original Systran/faster-whisper-large-v3-turbo deixou de existir; este e
    # publico, MIT, ~1M downloads/mes.
    ModelSpec("large-v3-turbo", "Large v3 Turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
              "whisper", 1500, "~3x", "Qualidade próxima do Large v3, ~2x mais rápido."),
    ModelSpec("large-v3", "Large v3", "Systran/faster-whisper-large-v3", "whisper",
              3000, "~0.7x", "Máxima precisão. Precisa de ~3 GB de RAM livres.",
              tag="máx. precisão"),
]

DIARIZATION_MODELS: list[ModelSpec] = [
    ModelSpec(
        "pyannote-community-1",
        "PyAnnote Speaker Diarization (community-1)",
        "pyannote/speaker-diarization-community-1",
        "diarization",
        30, "—",
        "Identifica falantes diferentes em reuniões e entrevistas.",
    ),
]

ALL_MODELS: list[ModelSpec] = WHISPER_MODELS + DIARIZATION_MODELS
_BY_KEY: dict[str, ModelSpec] = {m.key: m for m in ALL_MODELS}


def get_spec(key: str) -> ModelSpec:
    if key not in _BY_KEY:
        raise KeyError(f"Modelo desconhecido: {key!r}")
    return _BY_KEY[key]


# ---------- Listagem (estado do cache) ----------

def _hf_cache_root() -> Path:
    """Diretório onde o huggingface_hub guarda blobs/snapshots."""
    from huggingface_hub.constants import HF_HUB_CACHE
    return Path(HF_HUB_CACHE)


def _repo_dir(repo_id: str) -> Path:
    """Caminho local esperado para o repo no cache do HF."""
    return _hf_cache_root() / f"models--{repo_id.replace('/', '--')}"


def _dir_size_bytes(path: Path) -> int:
    """Soma do tamanho de arquivos reais (resolve symlinks pra contar blobs uma vez)."""
    if not path.exists():
        return 0
    total = 0
    seen_inodes: set[int] = set()
    for p in path.rglob("*"):
        try:
            st = p.stat()
        except (OSError, FileNotFoundError):
            continue
        if st.st_ino in seen_inodes:
            continue
        seen_inodes.add(st.st_ino)
        if p.is_file() or p.is_symlink():
            total += st.st_size
    return total


def model_status(spec: ModelSpec) -> dict[str, Any]:
    """Retorna {key, label, downloaded, size_bytes (real), ...} para um modelo."""
    repo_dir = _repo_dir(spec.repo_id)
    # Considera "baixado" se existir pelo menos um snapshot com model.bin/.safetensors válido.
    downloaded = False
    if repo_dir.exists():
        snapshots = repo_dir / "snapshots"
        if snapshots.exists():
            for snap in snapshots.iterdir():
                if any(snap.glob("*.bin")) or any(snap.glob("*.safetensors")) or any(snap.glob("config.yaml")):
                    downloaded = True
                    break

    return {
        "key": spec.key,
        "label": spec.label,
        "kind": spec.kind,
        "repo_id": spec.repo_id,
        "size_mb_estimate": spec.size_mb,
        "size_bytes_actual": _dir_size_bytes(repo_dir) if downloaded else 0,
        "speed_label": spec.speed_label,
        "description": spec.description,
        "tag": spec.tag,
        "downloaded": downloaded,
        "path": str(repo_dir) if downloaded else None,
    }


def list_models() -> dict[str, Any]:
    """Lista completa: whisper + diarização + uso de disco agregado."""
    whisper = [model_status(s) for s in WHISPER_MODELS]
    diarization = [model_status(s) for s in DIARIZATION_MODELS]
    total_bytes = sum(m["size_bytes_actual"] for m in whisper + diarization)
    return {
        "whisper": whisper,
        "diarization": diarization,
        "total_bytes": total_bytes,
        "cache_dir": str(_hf_cache_root()),
    }


# ---------- Download com progresso ----------

class _SilentTqdm:
    """Stub que satisfaz a interface tqdm sem renderizar nada.

    Usamos polling de disco (em ``download_model``) pra extrair o progresso real,
    então este stub só precisa não quebrar quando huggingface_hub o chama por
    debaixo dos panos. Implementamos o conjunto mínimo de métodos/atributos
    que as versões recentes da lib utilizam.
    """

    _lock = threading.RLock()

    def __init__(self, iterable: Any = None, *args: Any, **kwargs: Any) -> None:
        self.iterable = iterable
        self.total: int | None = kwargs.get("total")
        self.n: int = 0
        self.desc: str = kwargs.get("desc", "") or ""
        self.disable: bool = bool(kwargs.get("disable", False))
        self.pos: int = 0
        self.unit: str = kwargs.get("unit", "it")

    # huggingface_hub às vezes precisa de um lock estático compartilhado
    @classmethod
    def get_lock(cls) -> "threading.RLock":
        return cls._lock

    @classmethod
    def set_lock(cls, lock: Any) -> None:
        cls._lock = lock

    # ---- tqdm protocol (no-op) ----
    def update(self, n: int = 1) -> None:
        self.n += n

    def close(self) -> None:
        pass

    def clear(self, *_: Any, **__: Any) -> None: ...
    def refresh(self, *_: Any, **__: Any) -> None: ...
    def reset(self, total: int | None = None) -> None:
        if total is not None:
            self.total = total
        self.n = 0
    def set_description(self, desc: str | None = None, **_: Any) -> None:
        if desc is not None:
            self.desc = desc
    def set_description_str(self, desc: str | None = None, **_: Any) -> None:
        self.set_description(desc)
    def set_postfix(self, *_: Any, **__: Any) -> None: ...
    def set_postfix_str(self, *_: Any, **__: Any) -> None: ...
    def display(self, *_: Any, **__: Any) -> None: ...
    def write(self, *_: Any, **__: Any) -> None: ...

    @property
    def format_dict(self) -> dict[str, Any]:
        return {"n": self.n, "total": self.total, "elapsed": 0, "rate": 0}

    def __enter__(self) -> "_SilentTqdm":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def __iter__(self) -> Iterator[Any]:
        if self.iterable is None:
            return iter([])
        for x in self.iterable:
            yield x
            self.update(1)


def download_model(key: str, max_workers: int = 4) -> Iterator[dict[str, Any]]:
    """Baixa um modelo emitindo eventos pra UI.

    Eventos:
        {"type": "start", "model": ..., "size_mb": ...}
        {"type": "progress", "downloaded": int, "total": int, "current_file": str}
        {"type": "done", "model": ..., "size_bytes": int}
        {"type": "error", "message": str}
    """
    try:
        spec = get_spec(key)
    except KeyError as e:
        yield {"type": "error", "message": str(e)}
        return

    yield {"type": "start", "model": spec.key, "label": spec.label, "size_mb": spec.size_mb}

    # error_holder guarda mensagem + categoria pro frontend rotear o erro
    # corretamente (ex.: "no_token" / "no_access" abrem o wizard em Configuracoes).
    error_holder: dict[str, str] = {}

    def _do_download() -> None:
        try:
            snapshot_download(
                repo_id=spec.repo_id,
                tqdm_class=_SilentTqdm,
                max_workers=max_workers,
            )
        except GatedRepoError:
            # Conta nao aceitou os termos do modelo gated. Mais comum nos pyannote.
            error_holder["error"] = (
                f"Acesso negado a {spec.repo_id}. Faca login com a mesma conta "
                f"do token e aceite os termos em https://huggingface.co/{spec.repo_id}."
            )
            error_holder["kind"] = "no_access"
            error_holder["repo_id"] = spec.repo_id
        except RepositoryNotFoundError:
            # Pode ser repo inexistente OU repo privado sem acesso (HF retorna 404
            # em ambos pra nao vazar a existencia de repos privados).
            error_holder["error"] = (
                f"Modelo {spec.repo_id} nao encontrado ou inacessivel. Verifique "
                f"o token e se aceitou os termos em https://huggingface.co/{spec.repo_id}."
            )
            error_holder["kind"] = "not_found"
            error_holder["repo_id"] = spec.repo_id
        except HfHubHTTPError as e:
            s = str(e)
            if "401" in s:
                error_holder["error"] = (
                    "Token Hugging Face invalido ou ausente. Abra Configuracoes "
                    "para corrigir."
                )
                error_holder["kind"] = "no_token"
            elif "403" in s:
                error_holder["error"] = (
                    f"Acesso negado a {spec.repo_id}. Aceite os termos em "
                    f"https://huggingface.co/{spec.repo_id}."
                )
                error_holder["kind"] = "no_access"
                error_holder["repo_id"] = spec.repo_id
            else:
                error_holder["error"] = f"Erro HTTP: {e}"
                error_holder["kind"] = "http_error"
        except Exception as e:
            error_holder["error"] = f"{type(e).__name__}: {e}"
            error_holder["kind"] = "error"

    target = _repo_dir(spec.repo_id)
    total_estimate_bytes = spec.size_mb * 1024 * 1024

    # Roda o snapshot_download em background e emite progresso via polling
    # de tamanho do diretório alvo. Polling é mais robusto que tentar
    # interceptar o tqdm interno do huggingface_hub.
    thread = threading.Thread(target=_do_download, name=f"dl-{spec.key}", daemon=True)
    thread.start()

    last_bytes = 0
    last_change = time.monotonic()
    last_emit = 0.0

    while thread.is_alive():
        time.sleep(0.4)
        cur_bytes = _dir_size_bytes(target)
        if cur_bytes != last_bytes:
            last_bytes = cur_bytes
            last_change = time.monotonic()
        now = time.monotonic()
        # Throttle: emite no máximo 4× por segundo.
        if now - last_emit < 0.25:
            continue
        last_emit = now
        yield {
            "type": "progress",
            "downloaded": cur_bytes,
            "total": max(total_estimate_bytes, cur_bytes),
            "stalled": (now - last_change) > 30,
        }

    thread.join()

    if "error" in error_holder:
        event: dict[str, Any] = {"type": "error", "message": error_holder["error"]}
        if error_holder.get("kind"):
            event["error_kind"] = error_holder["kind"]
        if error_holder.get("repo_id"):
            event["repo_id"] = error_holder["repo_id"]
        yield event
        return

    final_size = _dir_size_bytes(target)
    yield {
        "type": "done",
        "model": spec.key,
        "label": spec.label,
        "size_bytes": final_size,
    }


# ---------- Delete ----------

def delete_model(key: str) -> dict[str, Any]:
    """Remove o cache de um modelo. Evicta também do _CURRENT_MODEL em RAM
    quando aplicável (senão o disco aparenta vazio mas a RAM ainda segura o
    modelo, o que confunde 'qual está ativo')."""
    spec = get_spec(key)

    # Evicta o modelo whisper carregado em RAM se for este.
    if spec.kind == "whisper":
        transcriber.evict_if_matches(spec.key)

    target = _repo_dir(spec.repo_id)
    if not target.exists():
        return {"removed": False, "reason": "not_downloaded", "path": str(target)}

    try:
        shutil.rmtree(target)
    except OSError as e:
        return {"removed": False, "reason": f"os_error: {e}", "path": str(target)}

    logger.info("Modelo removido do cache: %s -> %s", spec.key, target)
    return {"removed": True, "path": str(target)}


# ---------- Sanity check (usado pra startup logs) ----------

def summary_line() -> str:
    state = list_models()
    counts = sum(1 for m in state["whisper"] if m["downloaded"])
    total_gb = state["total_bytes"] / (1024 ** 3)
    return f"{counts}/{len(WHISPER_MODELS)} modelos Whisper em cache · {total_gb:.2f} GB total"


__all__ = [
    "ALL_MODELS",
    "WHISPER_MODELS",
    "DIARIZATION_MODELS",
    "ModelSpec",
    "get_spec",
    "list_models",
    "download_model",
    "delete_model",
    "summary_line",
]
