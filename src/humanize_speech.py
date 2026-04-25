"""V4.2 — Natural speech preprocessing.

The LLM happily emits "$4,273 at 4273 Mill Creek Road, call us at +18887775555".
Polly will read those character-by-character or with weird emphasis. A real
person says "forty-two seventy-three dollars at forty-two seventy-three Mill
Creek Road, call us at eight eight eight, seven seven seven, five five five
five." This module is the seam.

Pure functions. No deps. Reversible by toggling `humanize_speech: false` on
a tenant's YAML — defaults to true.

Rules applied (in order, each regex non-overlapping):
  1. Currency:        "$1,234.56" → "one thousand two hundred thirty-four dollars and fifty-six cents"
  2. Phone numbers:   "+18885551212" → "one, eight eight eight, five five five, one two one two"
                      "(555) 219-3987" → "five five five, two one nine, three nine eight seven"
  3. Times:           "9:30 AM" → "nine thirty A M". "3 PM" → "three P M"
  4. Street numbers:  4-digit + capitalized street → "forty-two seventy-three"
  5. Standalone ints: "I have 5 trucks" → unchanged (heuristics avoid over-eager rewrites)

Critical guarantee: this never raises. Any error in any rule passes through
the original text untouched, so a malformed input can't strip the AI's
voice mid-sentence.
"""
from __future__ import annotations

import re
from typing import Optional


_ONES = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four",
    5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
    10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
    14: "fourteen", 15: "fifteen", 16: "sixteen",
    17: "seventeen", 18: "eighteen", 19: "nineteen",
}
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty",
         6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"}


def _digit_word(d: str) -> str:
    return _ONES.get(int(d), d)


def _under_100(n: int) -> str:
    if n < 0 or n >= 100:
        return str(n)
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    if ones == 0:
        return _TENS[tens]
    return f"{_TENS[tens]}-{_ONES[ones]}"


def _under_1000(n: int) -> str:
    if n < 100:
        return _under_100(n)
    hundreds, rest = divmod(n, 100)
    if rest == 0:
        return f"{_ONES[hundreds]} hundred"
    return f"{_ONES[hundreds]} hundred {_under_100(rest)}"


def _int_to_words(n: int) -> str:
    """Up to 999,999. Beyond that, fall back to digit-by-digit."""
    if n < 0:
        return "negative " + _int_to_words(-n)
    if n < 1000:
        return _under_1000(n)
    if n < 1_000_000:
        thousands, rest = divmod(n, 1000)
        out = f"{_under_1000(thousands)} thousand"
        if rest:
            out += " " + _under_1000(rest)
        return out
    # Bigger than million → just say each digit
    return " ".join(_digit_word(d) for d in str(n))


def _street_pair(n: int) -> str:
    """4-digit street numbers: 4273 → 'forty-two seventy-three'.
    3-digit fall back to the full word ('four hundred fifty')."""
    if n < 100:
        return _under_100(n)
    if n < 1000:
        return _under_1000(n)
    if 1000 <= n < 10000:
        first, second = divmod(n, 100)
        if second == 0:
            return f"{_under_100(first)} hundred"
        return f"{_under_100(first)} {_under_100(second)}"
    return _int_to_words(n)


def _say_digits(s: str, group_size: int = None) -> str:
    """'5551234567' → 'five five five, one two three, four five six seven'
    when group_size is set in groups (3, 3, 4)."""
    digits = [_digit_word(d) for d in s if d.isdigit()]
    if not digits:
        return s
    if group_size is None:
        return " ".join(digits)
    out = []
    chunks = []
    if len(digits) == 10:
        chunks = [3, 3, 4]
    elif len(digits) == 11:
        chunks = [1, 3, 3, 4]
    elif len(digits) == 7:
        chunks = [3, 4]
    else:
        return " ".join(digits)
    idx = 0
    parts = []
    for c in chunks:
        parts.append(" ".join(digits[idx:idx + c]))
        idx += c
    return ", ".join(parts)


# ── currency ─────────────────────────────────────────────────────────

_CURRENCY_RE = re.compile(r"\$(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{1,2}))?")


def _spoken_currency(match) -> str:
    raw_int = match.group(1).replace(",", "")
    cents = match.group(2)
    try:
        n = int(raw_int)
    except ValueError:
        return match.group(0)
    dollars_word = _int_to_words(n)
    suffix_dollars = "dollar" if n == 1 else "dollars"
    if cents:
        cents_int = int(cents.ljust(2, "0"))
        if cents_int == 0:
            return f"{dollars_word} {suffix_dollars}"
        cents_word = _under_100(cents_int)
        cents_label = "cent" if cents_int == 1 else "cents"
        return f"{dollars_word} {suffix_dollars} and {cents_word} {cents_label}"
    return f"{dollars_word} {suffix_dollars}"


# ── phone numbers ────────────────────────────────────────────────────

# E.164: +1XXXXXXXXXX; bare 10/11-digit; or formatted "(XXX) XXX-XXXX" /
# "XXX-XXX-XXXX" / "XXX.XXX.XXXX".
_PHONE_RE = re.compile(
    r"\+\d{10,15}"
    r"|\(\d{3}\)\s*\d{3}[-.\s]\d{4}"
    r"|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"
    r"|\b\d{10,11}\b"
)


def _spoken_phone(match) -> str:
    raw = match.group(0)
    digits = "".join(c for c in raw if c.isdigit())
    return _say_digits(digits, group_size=True)


# ── times ────────────────────────────────────────────────────────────

_TIME_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*([AP]\.?M\.?)\b",
    flags=re.IGNORECASE,
)


def _spoken_time(match) -> str:
    h_str, m_str, ampm = match.group(1), match.group(2), match.group(3)
    try:
        h = int(h_str)
    except ValueError:
        return match.group(0)
    h_word = _under_100(h)
    if m_str:
        m = int(m_str)
        if m == 0:
            time_part = h_word
        elif m < 10:
            time_part = f"{h_word} oh {_under_100(m)}"
        else:
            time_part = f"{h_word} {_under_100(m)}"
    else:
        time_part = h_word
    ampm_clean = ampm.replace(".", "").upper()
    spoken_ampm = "A M" if ampm_clean == "AM" else "P M"
    return f"{time_part} {spoken_ampm}"


# ── street numbers ───────────────────────────────────────────────────

# Match 1-5 digit number followed by a capitalized word — likely street.
# We still require a street suffix in the lookahead so "42 calls" doesn't
# trip; "42 Oak St" does.
_STREET_RE = re.compile(r"\b(\d{1,5})\s+([A-Z][a-zA-Z]+)\b")

_STREET_SUFFIXES = {
    "Street", "St", "St.", "Road", "Rd", "Rd.", "Avenue", "Ave", "Ave.",
    "Boulevard", "Blvd", "Blvd.", "Drive", "Dr", "Dr.", "Lane", "Ln",
    "Ln.", "Court", "Ct", "Ct.", "Way", "Place", "Pl", "Pl.", "Highway",
    "Hwy", "Hwy.", "Parkway", "Pkwy", "Pkwy.", "Circle", "Cir", "Cir.",
    "Terrace", "Ter", "Ter.", "Trail", "Tr",
}


def _spoken_street(text: str) -> str:
    """Look ahead in the text to confirm this is actually a street
    address (suffix appears within ~5 words)."""
    def repl(m):
        try:
            num = int(m.group(1))
        except ValueError:
            return m.group(0)
        # Look for a street suffix within the next ~50 chars
        tail = text[m.end():m.end() + 60]
        if not any(suf in tail for suf in _STREET_SUFFIXES):
            return m.group(0)   # not a street — leave numeric
        return f"{_street_pair(num)} {m.group(2)}"
    return _STREET_RE.sub(repl, text)


# ── public API ───────────────────────────────────────────────────────

def humanize_for_speech(text: Optional[str]) -> str:
    """Apply all transforms. Empty / None passes through. Any internal
    failure returns the original text unchanged."""
    if not text:
        return text or ""
    try:
        out = text
        # 1. Currency first (before phone digit patterns can fire)
        out = _CURRENCY_RE.sub(_spoken_currency, out)
        # 2. Phone numbers
        out = _PHONE_RE.sub(_spoken_phone, out)
        # 3. Times
        out = _TIME_RE.sub(_spoken_time, out)
        # 4. Street addresses
        out = _spoken_street(out)
        return out
    except Exception:
        return text


def is_enabled(client: Optional[dict]) -> bool:
    """Per-tenant toggle. Default ON — opt-out by setting to false."""
    if client is None:
        return True
    val = client.get("humanize_speech")
    if val is None:
        return True
    return str(val).strip().lower() not in ("false", "0", "no")
