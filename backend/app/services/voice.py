"""Voice for the chat: dictation (speech-to-text) and read-aloud (text-to-speech) with the local audio models.

Runs synchronously on the ML worker (started on demand, kept warm while used): audio never leaves the Spark.
"""
from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..engines import get_engine
from ..models import ModelStatus, RegisteredModel
from ..settings_store import get_setting
from . import scheduler

MAX_AUDIO_BYTES = 25 * 1024 * 1024
AUDIO_EXT = {".webm", ".ogg", ".wav", ".mp3", ".m4a", ".mp4", ".flac"}


class VoiceError(Exception):
    def __init__(self, message: str, remediation: str | None = None):
        super().__init__(message)
        self.message, self.remediation = message, remediation


def _pick(db: Session, task: tuple[str, ...], preferred: str | None, lang: str | None = None) -> RegisteredModel | None:
    rows = db.scalars(select(RegisteredModel).where(RegisteredModel.backend == "mlworker", RegisteredModel.task.in_(task),
                                                    RegisteredModel.status.in_([ModelStatus.installed, ModelStatus.ready]))).all()
    if preferred:
        hit = next((m for m in rows if m.alias == preferred), None)
        if hit:
            return hit
    if lang:  # MMS voices are per language: mms-tts-spa, mms-tts-eng...
        code = {"es": "spa", "en": "eng", "ca": "cat", "fr": "fra", "de": "deu", "it": "ita", "pt": "por"}.get(lang, lang)
        hit = next((m for m in rows if m.alias.endswith(f"-{code}") or f"-{code}" in m.alias), None)
        if hit:
            return hit
    return rows[0] if rows else None


def status(db: Session) -> dict:
    asr = _pick(db, ("automatic-speech-recognition",), get_setting(db, "voice_asr_model"))
    tts = _pick(db, ("text-to-speech", "text-to-audio"), get_setting(db, "voice_tts_model"))
    return {"enabled": bool(get_setting(db, "voice_enabled")), "dictation": bool(asr), "read_aloud": bool(tts),
            "asr_model": asr.alias if asr else None, "tts_model": tts.alias if tts else None}


def _workdir() -> tuple[Path, str, str]:
    jid = uuid.uuid4().hex
    d = get_settings().outputs_dir / "mlworker" / jid
    d.mkdir(parents=True, exist_ok=True)
    return d, f"/data/outputs/{jid}", jid


def _engine_call(path: str, body: dict) -> dict:
    scheduler.ensure_engine("mlworker")
    try:
        return get_engine("mlworker").call(path, body)  # type: ignore[attr-defined]
    finally:
        scheduler.touch_engine("mlworker")


def transcribe(db: Session, data: bytes, filename: str, language: str | None = None) -> dict:
    if not get_setting(db, "voice_enabled"):
        raise VoiceError("La voz está desactivada.", "Un administrador puede activarla en Ajustes → Sistema.")
    m = _pick(db, ("automatic-speech-recognition",), get_setting(db, "voice_asr_model"))
    if not m:
        raise VoiceError("No hay ningún modelo de reconocimiento de voz instalado.",
                         "Un administrador puede instalar openai/whisper-large-v3-turbo en Descubrir modelos.")
    ext = Path(filename).suffix.lower()
    if ext not in AUDIO_EXT or not data or len(data) > MAX_AUDIO_BYTES:
        raise VoiceError("Audio no válido (máx. 25 MB: webm, ogg, wav, mp3, m4a).")
    work, inner, jid = _workdir()
    try:
        (work / f"input{ext}").write_bytes(data)
        res = _engine_call("/asr", {"job_id": jid, "model_path": f"/models/hf/{m.path}", "input": f"{inner}/input{ext}",
                                    "output_dir": inner, "language": language, "task": "transcribe", "timestamps": False})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"text": (res.get("text") or "").strip(), "language": res.get("language"), "model": m.alias,
            "duration_s": res.get("duration_s"), "processing_s": res.get("processing_s")}


def speakable(markdown: str) -> str:
    """Strip Markdown, code and links so the voice reads prose."""
    t = re.sub(r"```.*?```", " (bloque de código) ", markdown, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"^\s{0,3}(#{1,6}|[-*+]|\d+\.)\s+", "", t, flags=re.M)
    t = re.sub(r"[*_~>|#]+", " ", t)
    t = re.sub(r"\$[^$]*\$", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def speak(db: Session, text: str, lang: str | None = None, rate: float = 1.0) -> tuple[bytes, str]:
    if not get_setting(db, "voice_enabled"):
        raise VoiceError("La voz está desactivada.", "Un administrador puede activarla en Ajustes → Sistema.")
    m = _pick(db, ("text-to-speech", "text-to-audio"), get_setting(db, "voice_tts_model"), lang)
    if not m:
        raise VoiceError("No hay ninguna voz instalada.",
                         "Un administrador puede instalar facebook/mms-tts-spa (español) o mms-tts-eng en Descubrir modelos.")
    clean = speakable(text)[:4000]
    if not clean:
        raise VoiceError("No hay texto que leer.")
    work, inner, jid = _workdir()
    try:
        res = _engine_call("/tts", {"job_id": jid, "model_path": f"/models/hf/{m.path}", "text": clean,
                                    "output_dir": inner, "seed": 0, "speaking_rate": max(0.5, min(2.0, rate))})
        files = res.get("files") or []
        if not files:
            raise VoiceError("La voz no generó audio.")
        out = (work / files[0]).resolve()
        if work.resolve() not in out.parents:
            raise VoiceError("Ruta de audio no válida.")
        return out.read_bytes(), "audio/wav" if out.suffix == ".wav" else "audio/flac"
    finally:
        shutil.rmtree(work, ignore_errors=True)
