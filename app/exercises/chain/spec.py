"""The output spec both strategies must meet, and the CODE that checks it.

Checks are plain Python, not a model: deterministic, free and instant. That
is what makes the "gates" of a prompt chain cheap — and what lets this
exercise score both strategies objectively.
"""

import re
from dataclasses import dataclass

WORDS_MIN, WORDS_MAX = 150, 220
MAX_AVG_SENTENCE = 20
BANNED = ("leverage", "utilize", "paradigm", "synergy", "robust", "seamless")

SPEC_TEXT = f"""\
- First line: a title starting with "# ".
- Exactly 3 sections, each starting with a "## " heading.
- {WORDS_MIN}–{WORDS_MAX} words in total.
- Last line starts with "Key takeaway:" and is a single sentence.
- Short sentences: on average at most {MAX_AVG_SENTENCE} words per sentence.
- Plain words — never use: {", ".join(BANNED)}.
- Audience: a curious beginner."""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9’']+", re.sub(r"^#+\s*", "", text, flags=re.M))


def _sentences(text: str) -> list[str]:
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    return [s for s in re.split(r"(?<=[.!?])\s+", body.strip()) if _words(s)]


def check(text: str) -> list[Check]:
    lines = [l for l in text.strip().splitlines() if l.strip()]
    first, last = (lines[0], lines[-1]) if lines else ("", "")
    headings = [l for l in lines if l.startswith("## ")]
    n_words = len(_words(text))
    sentences = _sentences(text)
    avg = sum(len(_words(s)) for s in sentences) / max(1, len(sentences))
    found = sorted({w for w in BANNED if re.search(rf"\b{w}", text, re.I)})
    takeaway_sentences = len(_sentences(last[len("Key takeaway:"):])) if last.startswith("Key takeaway:") else 0
    return [
        Check("title", first.startswith("# ") and not first.startswith("## "), first[:60] or "(empty)"),
        Check("3 sections", len(headings) == 3, f"{len(headings)} '## ' headings"),
        Check("length", WORDS_MIN <= n_words <= WORDS_MAX, f"{n_words} words (want {WORDS_MIN}–{WORDS_MAX})"),
        Check("takeaway", last.startswith("Key takeaway:") and takeaway_sentences == 1,
              "ok" if last.startswith("Key takeaway:") else "last line isn't 'Key takeaway: …'"),
        Check("short sentences", avg <= MAX_AVG_SENTENCE, f"avg {avg:.1f} words/sentence"),
        Check("plain words", not found, ", ".join(found) or "none used"),
    ]
