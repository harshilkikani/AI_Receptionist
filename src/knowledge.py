"""V3.5 — Per-tenant knowledge base injection (RAG-lite).

Each client optionally provides `clients/<id>.knowledge.md` containing
sections the receptionist should quote from. When a caller mentions
keywords matching a section, that section is injected into the system
prompt (as a VOLATILE block — not cached, since it varies per-call).

Markdown format:

    # Pricing
    Pump-outs from $475 for 1000-gallon tanks.
    Emergency after-hours surcharge: $150.

    # Drain field repair
    We replace drain fields in 1-3 days.
    Quotes range $6000-$12000 based on size.

Matching is keyword-overlap: tokenize the caller message and each
section's header + body, score by intersection size, and inject the
top `max_sections` (default 2) hits above threshold `min_overlap`
(default 1 shared non-stopword).

Pure-Python, no deps, no vector DB. For ~5-10 sections per client this
is fine. Upgrade path: swap `_score_section` for an embedding lookup.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

_CLIENTS_DIR = Path(__file__).parent.parent / "clients"

# Common words that shouldn't count toward relevance
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "he", "her", "his", "i", "i'm", "im", "in", "is", "it",
    "its", "just", "like", "me", "my", "no", "not", "of", "on", "or",
    "our", "so", "that", "the", "their", "them", "they", "this", "to",
    "up", "was", "we", "were", "will", "with", "you", "your", "about",
    "got", "get", "yeah", "yes", "okay", "ok", "hi", "hey", "hello",
    "thanks", "thank", "please", "can", "could", "would", "should",
})

_TOKEN_RE = re.compile(r"[a-z0-9$]+")


def _tokenize(text: str) -> set:
    if not text:
        return set()
    return {t for t in _TOKEN_RE.findall(text.lower())
            if t not in _STOPWORDS and len(t) > 1}


def _kb_path(client_id: str) -> Path:
    return _CLIENTS_DIR / f"{client_id}.knowledge.md"


def _parse_kb(content: str) -> list:
    """Parse markdown into [{title, body, tokens}].

    Sections split on `# Header` lines. Nested headers (`##`, `###`)
    stay inside their parent section. Empty content returns [].
    """
    if not content:
        return []
    sections = []
    current = None
    for line in content.splitlines():
        stripped = line.rstrip()
        m = re.match(r"^#\s+(.+)$", stripped)
        if m:
            if current is not None:
                sections.append(current)
            current = {"title": m.group(1).strip(), "body_lines": []}
            continue
        if current is None:
            # Content before any header — make a default section
            current = {"title": "General", "body_lines": []}
        current["body_lines"].append(stripped)
    if current is not None:
        sections.append(current)

    out = []
    for s in sections:
        body = "\n".join(s["body_lines"]).strip()
        if not body and not s["title"]:
            continue
        tokens = _tokenize(s["title"] + " " + body)
        out.append({"title": s["title"], "body": body, "tokens": tokens})
    return out


@lru_cache(maxsize=32)
def load_kb(client_id: str) -> tuple:
    """Load and parse a client's knowledge file. Cached per process.
    Returns a tuple (for lru_cache hashability) of section dicts — as
    a tuple-of-frozen-dicts approximation, we return a tuple of
    (title, body, frozenset(tokens))."""
    p = _kb_path(client_id)
    if not p.exists():
        return tuple()
    try:
        content = p.read_text(encoding="utf-8")
    except OSError:
        return tuple()
    sections = _parse_kb(content)
    return tuple(
        (s["title"], s["body"], frozenset(s["tokens"]))
        for s in sections
    )


def reload_kb(client_id: Optional[str] = None):
    """Clear the cache — call after editing a knowledge file."""
    load_kb.cache_clear()


def _score_section(msg_tokens: set, section_tokens: frozenset) -> int:
    return len(msg_tokens & section_tokens)


def find_relevant(client_id: str, caller_message: str,
                  max_sections: int = 2, min_overlap: int = 1) -> list:
    """Return up to `max_sections` section bodies matching the caller's
    message. Empty list if no KB file or no hits above threshold."""
    kb = load_kb(client_id)
    if not kb or not caller_message:
        return []
    msg_tokens = _tokenize(caller_message)
    if not msg_tokens:
        return []
    scored = []
    for title, body, tokens in kb:
        score = _score_section(msg_tokens, tokens)
        if score >= min_overlap:
            scored.append((score, title, body))
    scored.sort(key=lambda t: -t[0])
    return [{"title": title, "body": body, "score": score}
            for score, title, body in scored[:max_sections]]


def build_kb_injection(client_id: str, caller_message: str) -> str:
    """Render relevant sections into a system-prompt-friendly string.
    Empty string if nothing matched."""
    hits = find_relevant(client_id, caller_message)
    if not hits:
        return ""
    lines = ["## Relevant knowledge (quote from these if applicable)"]
    for h in hits:
        lines.append(f"### {h['title']}")
        lines.append(h["body"])
    return "\n\n".join(lines)
