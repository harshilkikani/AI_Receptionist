"""V10.0 — conversational realism instrumentation.

Reads recent assistant turns from `transcripts` and reports the patterns
that make AI receptionists sound robotic:

  - Filler-prefix frequency (% of turns opening with "Hmm,", "Yeah, so",
    "Lemme see —", etc.)
  - Filler vocabulary diversity (distinct fillers / total filler-prefixed
    turns — higher = less templated)
  - Top repeated 3-5 word phrases across the assistant corpus
  - Pure-acknowledgment turn share ("Got it.", "Mhm.", "Okay.")
  - Top-10 sentence openers (any first word)

Use this as a before/after gate on realism changes. If the filler-prefix
share drops from 22% → 8% and the top-opener concentration drops from
35% → 18%, you've actually made the system less detectably AI.

Examples:
    python scripts/conversation_audit.py
    python scripts/conversation_audit.py --client septic_pro --days 14
    python scripts/conversation_audit.py --json
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# Known filler-shape openers. We match on the first ~2 words of the turn
# (lowercased), so this covers "Hmm,", "Hmm —", "Hmm so" all together.
_FILLER_TOKENS: tuple = (
    "hmm", "yeah", "right", "lemme", "okay", "alright", "sure",
    "so,", "well,", "actually,", "got", "mhm", "sounds", "perfect",
    "gotcha", "yep", "yup", "oh", "ah", "uh",
)

# Words we'd see at the start of a "pure-ack" turn — short, period-only
# replies the LLM emits to acknowledge without adding substance.
_ACK_SHAPES: tuple = (
    "got it.", "okay.", "mhm.", "sure thing.", "sounds good.",
    "perfect.", "alright.", "yeah.", "yep.", "right.",
    "yeah, no problem.", "no problem.", "of course.",
)


def _opener(text: str, n_words: int = 2) -> str:
    """Return the first n_words of the text, lowercased + stripped."""
    if not text:
        return ""
    words = re.findall(r"[A-Za-z']+", text)
    if not words:
        return ""
    return " ".join(words[:n_words]).lower()


def _starts_with_filler(text: str) -> Optional[str]:
    """Return the filler token if the turn opens with one. Empty otherwise."""
    if not text:
        return None
    first = (text.lstrip()[:30]).lower()
    for tok in _FILLER_TOKENS:
        if first.startswith(tok):
            return tok
    return None


def _is_pure_ack(text: str) -> bool:
    """A short ack-only turn. Bounded length + low information."""
    if not text:
        return False
    t = text.strip().lower()
    if len(t) > 30:
        return False
    return t in _ACK_SHAPES or any(t == s for s in _ACK_SHAPES)


def _ngrams(text: str, n: int = 4) -> list:
    """Lowercased word n-grams. Strips punctuation for stable matching."""
    words = re.findall(r"[A-Za-z']+", (text or "").lower())
    return [" ".join(words[i:i + n]) for i in range(len(words) - n + 1)]


def _fetch_turns(client_id: Optional[str], days: int) -> list:
    """Pull assistant turns from the transcripts table."""
    from src.usage import _connect, _db_lock, _init_schema
    from src.transcripts import _init_transcripts_schema
    cutoff = int(time.time()) - days * 86400
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        _init_transcripts_schema(conn)
        if client_id:
            rows = conn.execute(
                """SELECT client_id, text, ts FROM transcripts
                    WHERE role = 'assistant' AND client_id = ?
                      AND ts >= ?
                 ORDER BY ts DESC""",
                (client_id, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT client_id, text, ts FROM transcripts
                    WHERE role = 'assistant' AND ts >= ?
                 ORDER BY ts DESC""",
                (cutoff,),
            ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def audit(client_id: Optional[str] = None, days: int = 7,
          top_n: int = 10) -> dict:
    """Returns a structured report dict (also useful for tests)."""
    turns = _fetch_turns(client_id, days)
    total = len(turns)

    by_tenant: dict = collections.defaultdict(list)
    for t in turns:
        by_tenant[t["client_id"]].append(t["text"] or "")

    overall: dict = {
        "total_turns": total,
        "window_days": days,
        "tenant_filter": client_id or "<all>",
        "tenants": {},
    }

    for cid, texts in by_tenant.items():
        n = len(texts)
        filler_count = collections.Counter()
        opener_count = collections.Counter()
        ack_count = 0
        ngram_count = collections.Counter()

        for text in texts:
            if _is_pure_ack(text):
                ack_count += 1
            tok = _starts_with_filler(text)
            if tok:
                filler_count[tok] += 1
            opener_count[_opener(text, 2)] += 1
            for g in _ngrams(text, 4):
                ngram_count[g] += 1

        filler_total = sum(filler_count.values())
        distinct_fillers = len(filler_count)
        diversity = (distinct_fillers / filler_total) if filler_total else 1.0

        # Drop n-grams that are too generic (containing common stopwords-only)
        # to keep the top-list signal-rich.
        most_repeated = [
            (g, c) for g, c in ngram_count.most_common(top_n * 3)
            if c > 1
        ][:top_n]

        overall["tenants"][cid] = {
            "turns": n,
            "filler_prefixed": filler_total,
            "filler_pct": round(100 * filler_total / n, 1) if n else 0.0,
            "filler_diversity": round(diversity, 3),
            "top_fillers": filler_count.most_common(top_n),
            "pure_acks": ack_count,
            "pure_ack_pct": round(100 * ack_count / n, 1) if n else 0.0,
            "top_openers": opener_count.most_common(top_n),
            "top_repeated_phrases": most_repeated,
        }
    return overall


def _print_text_report(report: dict) -> None:
    print(f"\nconversation_audit · {report['tenant_filter']} · "
          f"last {report['window_days']}d · {report['total_turns']} turns\n")
    if not report["tenants"]:
        print("  (no assistant turns in window — try --days 30)")
        return
    for cid, t in sorted(report["tenants"].items()):
        print(f"== {cid} · {t['turns']} turns ==")
        print(f"  filler-prefixed:  {t['filler_prefixed']} ({t['filler_pct']}%)")
        print(f"  filler diversity: {t['filler_diversity']} "
              f"(1.0 = every filler unique; lower = more templated)")
        print(f"  pure-ack turns:   {t['pure_acks']} ({t['pure_ack_pct']}%)")
        if t["top_fillers"]:
            print("  top filler openers:")
            for tok, c in t["top_fillers"][:5]:
                print(f"    {c:>4}  {tok!r}")
        if t["top_openers"]:
            print("  top 2-word openers:")
            for op, c in t["top_openers"][:5]:
                print(f"    {c:>4}  {op!r}")
        if t["top_repeated_phrases"]:
            print("  most repeated 4-grams:")
            for g, c in t["top_repeated_phrases"][:6]:
                print(f"    {c:>4}  {g!r}")
        print()


def _cli(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        description="Audit assistant-turn realism across tenants.")
    p.add_argument("--client", default=None,
                   help="restrict to one tenant id (e.g. septic_pro)")
    p.add_argument("--days", type=int, default=7,
                   help="lookback window in days (default 7)")
    p.add_argument("--top", type=int, default=10,
                   help="top-N for ranked lists (default 10)")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:
        pass

    report = audit(client_id=args.client, days=args.days, top_n=args.top)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_text_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
