"""Built-in benchmark suites with deterministic scoring.

Quality suites are small, versioned, clearly defined tasks. They measure narrow abilities
(exact arithmetic, format following, factual multiple choice) — not general model quality.
"""
from __future__ import annotations

import json
import re

SUITES: dict[str, dict] = {
    "perf-short-v1": {
        "kind": "llm_perf",
        "description": "Performance: short prompt, 128 output tokens, concurrency sweep",
        "prompt": "Write a concise paragraph explaining what unified memory is in computer architecture.",
        "max_tokens": 128,
        "repeats": 3,
        "concurrency": [1, 4],
    },
    "perf-long-v1": {
        "kind": "llm_perf",
        "description": "Performance: ~2k-token prompt (prefill-heavy), 256 output tokens",
        "prompt": ("Summarise the following text in five bullet points.\n\n" +
                   ("The DGX Spark integrates a Grace CPU and a Blackwell GPU sharing coherent memory. " * 110)),
        "max_tokens": 256,
        "repeats": 2,
        "concurrency": [1],
    },
    "arith-exact-v1": {
        "kind": "llm_quality",
        "description": "Exact-match integer arithmetic (20 items). Score = fraction correct.",
        "scorer": "exact_int",
        "cases": [{"id": f"a{i}", "q": q, "a": a} for i, (q, a) in enumerate([
            ("17*23", 391), ("1234+5678", 6912), ("900-457", 443), ("144/12", 12), ("2**10", 1024),
            ("15*15", 225), ("1000-1", 999), ("37*3", 111), ("81/9", 9), ("123*4", 492),
            ("56+78", 134), ("7*8*9", 504), ("250/5", 50), ("999+1", 1000), ("13*17", 221),
            ("400-123", 277), ("6*7", 42), ("3**4", 81), ("64/8", 8), ("11*11", 121)])],
        "template": "Compute {q}. Answer with only the integer, no words.",
    },
    "json-format-v1": {
        "kind": "llm_quality",
        "description": "Instruction following: return valid JSON with exact keys (10 items).",
        "scorer": "json_keys",
        "cases": [{"id": f"j{i}", "q": t, "keys": ["name", "category"]} for i, t in enumerate([
            "an apple", "a hammer", "Madrid", "a violin", "the Moon", "a bicycle", "Python", "a rose", "gold", "Valencia"])],
        "template": ('Return ONLY a JSON object with exactly the keys "name" and "category" describing {q}. '
                     "No markdown, no extra text."),
    },
    "mcq-general-v1": {
        "kind": "llm_quality",
        "description": "Factual multiple choice (12 items), answer letter exact match.",
        "scorer": "letter",
        "cases": [
            {"id": "m1", "q": "Chemical symbol of sodium? A) So B) Na C) Sd D) N", "a": "B"},
            {"id": "m2", "q": "Largest planet in the Solar System? A) Earth B) Saturn C) Jupiter D) Neptune", "a": "C"},
            {"id": "m3", "q": "Capital of Spain? A) Barcelona B) Valencia C) Seville D) Madrid", "a": "D"},
            {"id": "m4", "q": "Boiling point of water at sea level in Celsius? A) 90 B) 100 C) 110 D) 120", "a": "B"},
            {"id": "m5", "q": "Author of 'Don Quixote'? A) Cervantes B) Lorca C) Machado D) Quevedo", "a": "A"},
            {"id": "m6", "q": "Speed of light is closest to? A) 3e5 km/s B) 3e3 km/s C) 3e7 km/s D) 300 km/s", "a": "A"},
            {"id": "m7", "q": "DNA stands for? A) Di-nitro acid B) Deoxyribonucleic acid C) Dual nucleic acid D) None", "a": "B"},
            {"id": "m8", "q": "Binary of decimal 5? A) 110 B) 111 C) 101 D) 100", "a": "C"},
            {"id": "m9", "q": "Which is a noble gas? A) Oxygen B) Nitrogen C) Argon D) Hydrogen", "a": "C"},
            {"id": "m10", "q": "Derivative of x^2? A) x B) 2x C) x^3 D) 2", "a": "B"},
            {"id": "m11", "q": "Ohm is the unit of? A) Current B) Voltage C) Power D) Resistance", "a": "D"},
            {"id": "m12", "q": "Year the Berlin Wall fell? A) 1989 B) 1991 C) 1979 D) 1985", "a": "A"},
        ],
        "template": "{q}\nAnswer with only the letter.",
    },
}


def _strip_reasoning(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def score(scorer: str, case: dict, output: str) -> float:
    out = _strip_reasoning(output)
    if scorer == "exact_int":
        m = re.findall(r"-?\d+", out.replace(",", ""))
        return 1.0 if m and int(m[-1]) == int(case["a"]) else 0.0
    if scorer == "letter":
        m = re.search(r"\b([ABCD])\b", out.upper())
        return 1.0 if m and m.group(1) == case["a"] else 0.0
    if scorer == "json_keys":
        s = out.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            obj = json.loads(s)
        except ValueError:
            return 0.0
        return 1.0 if isinstance(obj, dict) and sorted(obj.keys()) == sorted(case["keys"]) else 0.0
    raise ValueError(scorer)


def public_suites() -> list[dict]:
    return [{"id": k, "kind": v["kind"], "description": v["description"],
             "items": len(v.get("cases", [])) or v.get("repeats"), "scorer": v.get("scorer")} for k, v in SUITES.items()]
