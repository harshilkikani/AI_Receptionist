"""V4.4 — Strict grounding (anti-hallucination).

The LLM happily quotes prices that don't exist. Before this module: a
caller asking "how much for a fan motor swap?" might hear "$249", which
the operator never priced. Customer hears it, expects it, gets angry
when the real bill is $385.

Strict grounding:
  1. Builds the set of "allowed" $-prices from the tenant's pricing_summary
     + knowledge.md.
  2. Scans the reply for any $-amount.
  3. If a quoted price isn't in the allowed set (and no nearby price is
     ±20% close — to permit the LLM rounding "$475-$525" to "$500"),
     the offending SENTENCE is replaced with a safe fallback that
     promises a callback with an exact number.

Per-tenant `strict_grounding: true|false` flag, default true for v4+.
Always falls back to the original reply on internal exceptions.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("grounding")


_PRICE_RE = re.compile(r"\$(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{1,2}))?")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")

_FALLBACK_LINE = (
    "Let me check the exact number — I'll have someone call you "
    "right back with it."
)


def _extract_prices(text: str) -> list:
    """Return prices in `text` as floats (dollars + cents)."""
    out = []
    for m in _PRICE_RE.finditer(text or ""):
        try:
            dollars = int(m.group(1).replace(",", ""))
        except ValueError:
            continue
        cents = int((m.group(2) or "0").ljust(2, "0"))
        out.append(dollars + cents / 100.0)
    return out


def _allowed_prices(client: Optional[dict]) -> set:
    """Collect every dollar amount mentioned anywhere in the tenant's
    grounded sources. Returns a set of floats."""
    if client is None:
        return set()
    sources = [
        client.get("pricing_summary") or "",
        client.get("services") or "",
        client.get("hours") or "",
        client.get("service_area") or "",
    ]
    # Pull from knowledge.md if available
    try:
        from src import knowledge
        kb = knowledge.load_kb(client.get("id") or "")
        for _title, body, _tokens in kb:
            sources.append(body)
    except Exception:
        pass

    prices = set()
    for src in sources:
        for p in _extract_prices(src):
            prices.add(p)
    return prices


def _is_close_enough(quoted: float, allowed: set, tolerance: float = 0.20) -> bool:
    """A quote within ±tolerance of any allowed price counts as grounded.
    Lets the LLM round '$475-$525' to '$500' or say '~$475' without
    triggering a violation."""
    for a in allowed:
        if a <= 0:
            continue
        if abs(quoted - a) / a <= tolerance:
            return True
    return False


def verify_reply(reply: Optional[str],
                 client: Optional[dict]) -> tuple:
    """Return (verified_reply, violations).

    `violations` is a list of {price_quoted, replaced} dicts so the
    operator (and /metrics later) can audit how often the LLM tried to
    invent a number.
    """
    if not reply:
        return reply or "", []

    if not is_enabled(client):
        return reply, []

    try:
        allowed = _allowed_prices(client)
        if not allowed:
            # If the tenant has NO grounded prices, we can't ground
            # anything — pass through. Operator is choosing to skip
            # strict grounding by leaving pricing_summary empty.
            return reply, []

        violations = []
        # Find every price in the reply
        prices_in_reply = _extract_prices(reply)
        if not prices_in_reply:
            return reply, []

        # Determine which prices are violators
        bad = [p for p in prices_in_reply if not _is_close_enough(p, allowed)]
        if not bad:
            return reply, []

        # Replace each sentence containing a bad price with the fallback
        sentences = _SENTENCE_BOUNDARY_RE.split(reply)
        cleaned_sentences = []
        replaced_any = False
        for s in sentences:
            sentence_prices = _extract_prices(s)
            if any(p in bad for p in sentence_prices):
                violations.append({
                    "prices_quoted": [p for p in sentence_prices if p in bad],
                    "replaced": True,
                    "original_sentence": s.strip(),
                })
                cleaned_sentences.append(_FALLBACK_LINE)
                replaced_any = True
            else:
                cleaned_sentences.append(s)

        out = " ".join(s.strip() for s in cleaned_sentences if s.strip())
        # If everything got replaced, reply is just the fallback —
        # de-duplicate to a single instance so we don't repeat it.
        if replaced_any:
            # Collapse repeated fallback lines into one
            out = re.sub(
                rf"({re.escape(_FALLBACK_LINE)}\s*)+",
                _FALLBACK_LINE + " ",
                out,
            ).strip()

        if violations:
            log.warning(
                "grounding violation client=%s prices=%s",
                (client or {}).get("id"),
                [v["prices_quoted"] for v in violations],
            )

        return out, violations
    except Exception as e:
        log.error("grounding verify_reply raised: %s", e)
        return reply, []


def is_enabled(client: Optional[dict]) -> bool:
    """Per-tenant toggle. Default ON for v4+."""
    if client is None:
        return True
    val = client.get("strict_grounding")
    if val is None:
        return True
    return str(val).strip().lower() not in ("false", "0", "no")
