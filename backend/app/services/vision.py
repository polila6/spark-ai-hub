"""A small vision model that looks at pictures and document pages for the assistant (tool view_image).

The main model of a workspace may be a text model, or too busy and too large to spend on screenshots: a helper of a few billion
parameters reads the page and says what is wrong (text cut off or overprinted, tables outside the margin, legends over data,
placeholder pictures, blurry images) so the assistant can fix it. The helper is chosen by the admin setting
`vision_helper_model`; "auto" picks a small installed vision model, else a loaded one, else any installed vision model."""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ModelStatus, RegisteredModel
from ..settings_store import get_setting
from . import llm, scheduler

SMALL_GB = 9.0  # a model up to this memory is a dedicated "small helper"
USABLE = (ModelStatus.installed, ModelStatus.ready, ModelStatus.loading, ModelStatus.busy)
MAX_SIDE = 1600
MAX_PAGES_PER_CALL = 6

REVIEW_PROMPT = (
    "Eres un revisor visual de documentos. Mira la(s) imagen(es) (páginas de un documento, una gráfica o una tabla) y enumera "
    "SOLO defectos reales y visibles, cada uno con la página (si hay varias) y la zona (arriba/centro/abajo, izquierda/derecha): "
    "texto cortado por el borde o por el margen; texto encima de otro texto; tablas o figuras que se salen de la página o de su "
    "caja; leyendas que tapan datos; etiquetas de ejes ilegibles o amontonadas; imágenes borrosas, deformadas, vacías o que son un "
    "hueco/«tarjeta» en lugar de la foto prevista; páginas casi en blanco; cabeceras o números de página que invaden el texto; "
    "caracteres raros o «??» en lugar de referencias. Para cada defecto propone en una frase cómo corregirlo (por ejemplo «reducir "
    "el ancho de la tabla», «acortar el título», «mover la leyenda debajo»). No inventes defectos: si algo está bien, no lo "
    "menciones. Máximo 8 defectos, sin repetir ninguno y cada uno en una sola línea. Si no ves ningún problema, responde "
    "exactamente «OK: sin defectos visibles». Responde en español, conciso."
)


def _usable(m: RegisteredModel) -> bool:
    caps = set(m.capabilities or [])
    return "vision" in caps and "chat" in caps and m.status in USABLE and m.backend in ("vllm", "ollama")


def choose(models: list, setting: str = "auto", current=None):
    """Pure choice of the helper among the models given (see helper_model)."""
    cands = [m for m in models if _usable(m)]
    if setting and setting != "auto":
        named = next((m for m in cands if m.alias == setting), None)
        if named is not None:
            return named
    if not cands:
        return None
    small = sorted((m for m in cands if (m.estimated_memory_gb or 99) <= SMALL_GB), key=lambda m: m.estimated_memory_gb or 99)
    if small:
        return small[0]
    loaded = [m for m in cands if m.status in (ModelStatus.ready, ModelStatus.busy)]
    if current is not None and current in cands:
        return current
    return (loaded or sorted(cands, key=lambda m: m.estimated_memory_gb or 99))[0]


def helper_model(db: Session, current: RegisteredModel | None = None) -> RegisteredModel | None:
    """The model that will look at pictures, or None when no vision model is installed."""
    return choose(list(db.scalars(select(RegisteredModel))), str(get_setting(db, "vision_helper_model") or "auto").strip(), current)


def available(db: Session) -> bool:
    return helper_model(db) is not None


def downscale(data: bytes, max_side: int = MAX_SIDE) -> bytes:
    """PNG/JPEG bytes whose long side is at most `max_side` (small models read better at a moderate size)."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        return buf.getvalue()


def parse_pages(spec: str | int | None, total: int, limit: int = MAX_PAGES_PER_CALL) -> list[int]:
    """"1-3,7" → [1,2,3,7] clamped to the document and to `limit`; empty/None → the first pages."""
    if spec in (None, "", "all"):
        return list(range(1, min(total, limit) + 1))
    out: list[int] = []
    for part in re.split(r"[,\s]+", str(spec).strip()):
        if not part:
            continue
        a, _, b = part.partition("-")
        try:
            lo, hi = int(a), int(b or a)
        except ValueError:
            continue
        out += range(min(lo, hi), max(lo, hi) + 1)
    seen, pages = set(), []
    for p in out:
        if 1 <= p <= total and p not in seen:
            seen.add(p)
            pages.append(p)
    return pages[:limit]


def render_pdf(path: Path, pages: list[int], scale: float = 1.5) -> list[tuple[int, bytes]]:
    import pypdfium2 as pdfium

    from .web import _PDFIUM

    out = []
    with _PDFIUM:
        pdf = pdfium.PdfDocument(str(path))
        try:
            for p in pages:
                if 1 <= p <= len(pdf):
                    img = pdf[p - 1].render(scale=scale).to_pil().convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, "PNG", optimize=True)
                    out.append((p, buf.getvalue()))
        finally:
            pdf.close()
    return out


def pdf_pages(path: Path) -> int:
    import pypdfium2 as pdfium

    from .web import _PDFIUM

    with _PDFIUM:
        pdf = pdfium.PdfDocument(str(path))
        try:
            return len(pdf)
        finally:
            pdf.close()


PLAIN_PROMPT = "Eres un asistente que analiza imágenes con precisión. Describe solo lo que se ve y responde en español, conciso."


def analyze(model: RegisteredModel, images: list[bytes], question: str | None = None, labels: list[str] | None = None,
            max_tokens: int = 700, wait_s: int = 900) -> str:
    """Ask the vision model about the pictures (one call). `labels` names each picture in the prompt (e.g. «página 4»).
    Without a `question` it reviews them for layout defects (REVIEW_PROMPT); with one it answers that question."""
    inst = scheduler.ensure_loaded(model.id, wait_s=wait_s)
    scheduler.begin_request(inst.id)
    try:
        parts: list[dict] = []
        for i, data in enumerate(images):
            if labels and i < len(labels):
                parts.append({"type": "text", "text": f"[{labels[i]}]"})
            parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(downscale(data)).decode()}})
        parts.append({"type": "text", "text": (question or "Revisa estas imágenes y enumera los defectos.")[:4000]})
        body = {"messages": [{"role": "system", "content": PLAIN_PROMPT if question else REVIEW_PROMPT},
                             {"role": "user", "content": parts}],
                "max_tokens": max_tokens, "temperature": 0.3, "top_p": 0.8,
                "presence_penalty": 1.5, "frequency_penalty": 0.5}  # small models loop without these
        if model.backend == "vllm":
            body["chat_template_kwargs"] = {"enable_thinking": False}
        r = llm.chat_once(model, body)
    finally:
        scheduler.end_request(inst.id)
    text = (r.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
