"""Detect a model that has fallen into a loop while it reasons or writes (the same sentences again and again).

Local reasoning models sometimes circle: «Wait, let me check… Actually, I should check… Wait, let me check…» until the token
budget is gone, minutes of GPU for nothing. LoopGuard looks at the rolling tail of what a step produces and says why it
thinks the text repeats, so the runner can cut the stream at once and make the model act instead of deliberating again.
A fourth, optional one: `max_total` characters in one step (reasoning that never ends is as costly as a loop).
Three signals, all on the last ~9000 characters:
  - one sentence (≥ 28 letters) appears ≥ 4 times, or ≥ 5 sentences start the same way;
  - the last 300 characters already appeared ≥ 3 times (a block repeated);
  - the text hardly compresses (an almost identical text; real prose compresses to ~35-45 %).
"""
from __future__ import annotations

import re
import zlib
from collections import Counter

WINDOW = 9000
MIN_CHARS = 1500  # never judge less text than this
CHECK_EVERY = 500  # characters between two checks (the checks are cheap but not free)
SENTENCE_MIN = 28
SENTENCE_REPEATS = 4
PREFIX_REPEATS = 6
TAIL_CHARS = 300
TAIL_REPEATS = 3
COMPRESS_MIN = 3500
COMPRESS_RATIO = 0.13
REASONING_MAX_CHARS = 40000  # ≈ 11k tokens of reasoning in ONE step without a single action: cut it


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def detect(text: str) -> str | None:
    """Why `text` looks like a loop (a short Spanish reason), or None."""
    if len(text) < MIN_CHARS:
        return None
    sents = [_norm(s) for s in re.split(r"(?<=[.!?:;])\s+|\n+", text)]
    sents = [s for s in sents if len(s) >= SENTENCE_MIN]
    if sents:
        top, n = Counter(sents).most_common(1)[0]
        if n >= SENTENCE_REPEATS:
            return f"la misma frase {n} veces: «{top[:70]}»"
        pre, m = Counter(s[:42] for s in sents).most_common(1)[0]
        if m >= PREFIX_REPEATS:
            return f"{m} frases que empiezan igual: «{pre[:50]}…»"
    flat = re.sub(r"\s+", " ", text)
    if len(flat) >= TAIL_CHARS * TAIL_REPEATS:
        tail = flat[-TAIL_CHARS:]
        if flat.count(tail) >= TAIL_REPEATS:
            return "un bloque de texto que se repite"
    if len(text) >= COMPRESS_MIN:
        raw = text[-COMPRESS_MIN:].encode("utf-8", "ignore")
        if len(zlib.compress(raw, 6)) / max(1, len(raw)) < COMPRESS_RATIO:
            return "texto casi idéntico repetido"
    return None


def cut_repeat(text: str) -> str:
    """`text` without its repeated tail: everything up to where the last 300 characters first appeared (for a looping answer)."""
    flat = text
    if len(flat) < TAIL_CHARS * 2:
        return text
    tail = flat[-TAIL_CHARS:]
    first = flat.find(tail)
    if 0 <= first < len(flat) - TAIL_CHARS:
        return flat[:first + TAIL_CHARS].rstrip()
    return text


class LoopGuard:
    """Feed it the pieces of a stream; `feed` returns the reason the first time the text looks like a loop."""

    def __init__(self, min_chars: int = MIN_CHARS, check_every: int = CHECK_EVERY, window: int = WINDOW,
                 max_total: int | None = None):
        self.min_chars, self.check_every, self.window, self.max_total = min_chars, check_every, window, max_total
        self.buf = ""
        self.total = 0
        self._since = 0
        self.reason: str | None = None

    def feed(self, piece: str) -> str | None:
        if self.reason or not piece:
            return self.reason
        self.buf = (self.buf + piece)[-self.window:]
        self.total += len(piece)
        self._since += len(piece)
        if self.max_total and self.total >= self.max_total:
            self.reason = f"{self.total:,} caracteres de razonamiento sin actuar".replace(",", ".")
            return self.reason
        if self.total < self.min_chars or self._since < self.check_every:
            return None
        self._since = 0
        self.reason = detect(self.buf)
        return self.reason
