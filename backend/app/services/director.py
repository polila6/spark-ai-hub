"""The director: before an answer starts, decide which of the capabilities the user has *allowed* this message really needs.

Thinking, internet, deep research, skills, agents and code are permissions, not obligations. Offering all of them on
every message makes simple questions slow (long reasoning, tool definitions in the prompt, wasted search steps), so a
small decision comes first and the answer then runs with only what that message needs.

Per capability the user picks `auto` (the director decides), `on` (always) or `off` (never). A slider sets how far the
director may go: 0 instant, 1 fast, 2 balanced, 3 deep, 4 maximum. Cheap rules decide whenever the message is clear;
the model is asked only when it is not (and never at the two fastest levels). If anything fails the answer runs with
everything that was allowed, exactly as before the director existed.

Pure logic (no database, no network); the model call is injected, so all of this is unit tested.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

CAPS = ("think", "web", "research", "skills", "agents", "code")
STATES = ("auto", "on", "off")
DEFAULT_ALLOW = {"think": "auto", "web": "auto", "research": "off", "skills": "auto", "agents": "auto", "code": "auto"}
LEVELS = ("instant", "fast", "balanced", "deep", "max")
STEP_CAP = {0: 3, 1: 8}  # chat only: the fastest levels do not wander through long tool loops
LLM_TIMEOUT_TOKENS = 120


def normalize_allow(allow: dict | None, legacy: dict | None = None) -> dict[str, str]:
    """Full capability map. `legacy` carries the old booleans (thinking, web, research) for clients that predate it."""
    out = dict(DEFAULT_ALLOW)
    if legacy:
        if legacy.get("thinking") is True:
            out["think"] = "on"
        elif legacy.get("thinking") is False:
            out["think"] = "off"
        if legacy.get("web") is False:
            out["web"] = "off"
        if legacy.get("research") is True:
            out["research"] = "on"
    for k, v in (allow or {}).items():
        if k in out and v in STATES:
            out[k] = v
    return out


@dataclass
class Ask:
    content: str
    level: int = 2
    allow: dict = field(default_factory=dict)
    available: set[str] = field(default_factory=set)  # capabilities that exist right now (settings, model, role)
    workspace: bool = False
    attachments: list[str] = field(default_factory=list)  # file names
    previous: str = ""  # start of the assistant's last message, for follow-ups
    es: bool = True
    director: bool = True  # False: no deciding, everything allowed is offered (the pre-director behaviour)


@dataclass
class Decision:
    think: bool = False
    web: bool = False
    research: bool = False
    skills: bool = False
    agents: bool = False
    code: bool = False
    steps: int | None = None  # tool-step cap for this answer (None = the usual limit)
    level: int = 2
    source: str = "rules"  # rules | llm | fixed | fallback
    reason: str = ""
    ms: float = 0.0

    def tools(self) -> list[str]:
        return [c for c in ("web", "research", "skills", "agents", "code") if getattr(self, c)]

    def public(self) -> dict:
        return {"think": self.think, "tools": self.tools(), "level": self.level, "source": self.source,
                "reason": self.reason, "ms": round(self.ms, 1), "steps": self.steps}


# ------------------------------------------------------------------ cheap rules

_GREET = (r"hola|buenas|buenos d[ií]as|buenas tardes|buenas noches|hey|hi|hello|gracias|muchas gracias|thanks|thank you|"
          r"adi[oó]s|hasta luego|bye")
# «sí», «vale»… only close a conversation when nothing was asked: after a question they ANSWER it («¿busco en la web?» → «sí»)
_CONFIRM = (r"vale|ok|okay|perfecto|genial|de acuerdo|entendido|claro|estupendo|muy bien|bien|s[ií]|no")
GREETING = re.compile(rf"^\W*(?:(?:{_GREET}|{_CONFIRM})(?![\w])[\s\W]*){{1,4}}$", re.I)
CONFIRM_WORD = re.compile(rf"(?<![\w])(?:{_CONFIRM})(?![\w])", re.I)
URL = re.compile(r"https?://\S+|\bwww\.\S+", re.I)
WEB_WORDS = re.compile(r"\b(busca|buscar|b[uú]scame|googlea|investiga en internet|en internet|en la web|online|noticias?|[uú]ltim[oa]s?|"
                       r"recientes?|actual(?:es|idad)?|hoy|ayer|esta semana|este mes|cotizaci[oó]n|precio(?:s)? (?:de|del|actual)|"
                       r"versi[oó]n m[aá]s reciente|search the web|look up|latest|news|current|today|right now|price of|"
                       r"what'?s new|release notes)\b", re.I)
CODE_WORDS = re.compile(r"(```|\b(ejecuta|ejec[uú]talo|corre el|script|programa|python|pandas|gr[aá]fic[ao]s?|plot|histograma|"
                        r"regresi[oó]n|simula|simulaci[oó]n|calcula|csv|excel|xlsx|dataset|datos adjuntos|run (?:this|the) code|"
                        r"chart|analy[sz]e the data)\b)", re.I)
THINK_WORDS = re.compile(r"\b(demuestra|demostrar|prueba que|razona|paso a paso|analiza|an[aá]lisis (?:profundo|detallado)|compara|"
                         r"optimiza|dise[nñ]a|arquitectura|depura|debug|bug|por qu[eé] falla|estrategia|resuelve|ecuaci[oó]n|integral|"
                         r"derivada|algoritmo|complejidad|trade-?offs?|pros y contras|planifica|plan detallado|prove|derive|reason|"
                         r"step by step|why does|design|architecture|optimi[sz]e)\b", re.I)
RESEARCH_WORDS = re.compile(r"\b(investiga a fondo|investigaci[oó]n (?:a fondo|profunda|exhaustiva)|estado del arte|revisi[oó]n bibliogr[aá]fica|"
                            r"informe (?:completo|detallado|exhaustivo) sobre|deep research|literature review|in-depth report)\b", re.I)
AGENT_WORDS = re.compile(r"\b(en paralelo|varios agentes|agentes en paralelo|in parallel|several agents|divide (?:la|el) tarea)\b", re.I)
SKILL_WORDS = re.compile(r"\b(docx|pptx|xlsx|word|powerpoint|presentaci[oó]n|informe|documento|pdf|plantilla|memoria|word document|slides?|"
                         r"report|template)\b", re.I)


@dataclass
class Signals:
    trivial: bool = False
    web: bool = False
    code: bool = False
    think: bool = False
    research: bool = False
    agents: bool = False
    skills: bool = False
    confident: bool = False  # the rules are enough; do not ask the model
    why: list[str] = field(default_factory=list)


def classify(ask: Ask) -> Signals:
    text = (ask.content or "").strip()
    s = Signals()
    if not text:
        return s
    if GREETING.match(text) and not ask.attachments and not (ask.previous and CONFIRM_WORD.search(text)):
        s.trivial = s.confident = True
        s.why.append("saludo o confirmación")
        return s
    if URL.search(text):
        s.web, s.confident = True, True
        s.why.append("incluye una dirección web")
        text = URL.sub(" ", text)  # the words inside an address say nothing about what is wanted
    if WEB_WORDS.search(text):
        s.web, s.confident = True, True
        s.why.append("pide información actual")
    if RESEARCH_WORDS.search(text):
        s.research = s.web = s.agents = s.confident = True
        s.why.append("pide una investigación a fondo")
    if AGENT_WORDS.search(text):
        s.agents = s.confident = True
    if CODE_WORDS.search(text) or any(re.search(r"\.(csv|xlsx?|tsv|json|parquet|py|ipynb)$", a, re.I) for a in ask.attachments):
        s.code, s.confident = True, True
        s.why.append("hay código o datos que procesar")
    if THINK_WORDS.search(text) or len(text) > 700:
        s.think = True
        s.why.append("requiere razonar")
    if SKILL_WORDS.search(text):
        s.skills = True
    if s.think and not (s.web or s.code) and len(text) > 300:
        s.confident = True  # a long reasoning task: no need to ask
    return s


# ------------------------------------------------------------------ the model's opinion (only when the rules are unsure)

PROMPT = ("You route requests for an assistant. Decide which capabilities the user's LAST message needs so it is answered "
          "as fast as possible without losing accuracy. Reply with ONLY a JSON object: "
          '{"think":bool,"web":bool,"code":bool,"skills":bool,"agents":bool,"research":bool,"reason":"<=8 words"}. '
          "think: multi-step reasoning, maths, debugging, planning. web: current or external facts, or a URL. code: run "
          "code, analyse data files, make charts or files. skills: office documents or specialised procedures. agents: only "
          "big parallel jobs. research: only an explicit in-depth research report. Everything false for chit-chat, simple "
          "questions, translation, rewriting, or explanations from general knowledge. Only choose among the available ones.")


def build_prompt(ask: Ask, candidates: set[str]) -> list[dict]:
    body = (f"Available: {', '.join(sorted(candidates)) or 'none'}\n"
            f"Attachments: {', '.join(ask.attachments[:8]) or 'none'}\n"
            + (f"Assistant's previous message (start): {ask.previous[:300]}\n" if ask.previous else "")
            + f"User message: {ask.content[:1500]}")
    return [{"role": "system", "content": PROMPT}, {"role": "user", "content": body}]


def parse_opinion(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    out = {k: bool(data.get(k)) for k in ("think", "web", "code", "skills", "agents", "research")}
    out["reason"] = str(data.get("reason") or "")[:80]
    return out


# ------------------------------------------------------------------ the decision


def decide(ask: Ask, complete: Callable[[list[dict]], str] | None = None) -> Decision:
    t0 = time.perf_counter()
    level = max(0, min(4, int(ask.level)))
    allow = normalize_allow(ask.allow)
    avail = set(ask.available) | {"think"}
    forced = {c for c in CAPS if allow[c] == "on" and c in avail}
    candidates = {c for c in CAPS if allow[c] == "auto" and c in avail}
    d = Decision(level=level)
    d.steps = None if ask.workspace or forced else STEP_CAP.get(level)  # forcing a capability means "do the work"

    def finish(want: set[str], source: str, reason: str) -> Decision:
        use = forced | (want & candidates)
        if ask.workspace:  # a workspace task runs on its commands and delegates: none of that is taken away mid-task
            use |= {c for c in ("code", "skills", "agents") if c in avail and allow[c] != "off"}
        d.think = "think" in use
        for c in ("web", "research", "skills", "agents", "code"):
            setattr(d, c, c in use)
        if d.research:  # deep research needs the web and agents
            d.web = d.web or "web" in avail
            d.agents = d.agents or "agents" in avail
        d.source, d.reason = source, reason
        d.ms = (time.perf_counter() - t0) * 1000
        return d

    if not ask.director:
        # the old behaviour: offer everything allowed; thinking follows the slider
        want = set(candidates) - {"research", "think"}
        if level >= 3:
            want.add("think")
        return finish(want, "fixed", "director desactivado")

    sig = classify(ask)
    want: set[str] = set()
    source, why = "rules", ", ".join(sig.why) or "sin señales claras"
    if sig.trivial:
        pass
    elif sig.confident or level <= 1 or complete is None:
        want = {c for c, on in (("think", sig.think), ("web", sig.web), ("code", sig.code), ("skills", sig.skills),
                                ("agents", sig.agents), ("research", sig.research)) if on}
        if not sig.confident and level >= 2 and complete is None:  # unsure and nobody to ask: keep everything allowed
            want = set(candidates) - {"research"}
            source, why = "fallback", "sin criterio claro: se ofrece todo lo permitido"
    else:
        try:
            op = parse_opinion(complete(build_prompt(ask, candidates)))
        except Exception:  # noqa: BLE001 — a failing director must never stop the answer
            op = None
        if op is None:
            want = set(candidates) - {"research"}
            source, why = "fallback", "el director no respondió: se ofrece todo lo permitido"
        else:
            want = {c for c in CAPS if op.get(c)}
            source, why = "llm", op["reason"] or "decidido por el director"

    # the slider sets how far thinking goes
    if level == 0 or level == 1:
        want.discard("think")
    if level == 0:
        want &= {"think"}  # nothing is offered unprompted at the instant level (forced capabilities still are)
    elif level >= 3 and not sig.trivial and "think" in candidates:
        want.add("think")
        why += " · nivel profundo: razona"
    if level >= 4 and not sig.trivial and "think" in candidates:
        want.add("think")
    return finish(want, source, why)
