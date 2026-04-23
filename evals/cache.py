"""V3.16 — eval response cache.

Eval runs are expensive: every case is an LLM call. Once the cases.jsonl
file stabilizes, repeat runs hit Claude with the exact same prompts.
This cache hashes (case_id, final_user_message, client_id) and stores
the ChatResponse shape in data/eval_cache.jsonl. A second run with no
prompt changes costs $0.

Cache miss when: the case was never run, or the prompt changed, or the
user explicitly passes --no-cache.

Invalidation: delete data/eval_cache.jsonl. Cache entries older than
MAX_AGE_DAYS are also ignored to avoid serving stale behavior.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("evals.cache")

_ROOT = Path(__file__).parent.parent
CACHE_PATH = _ROOT / "data" / "eval_cache.jsonl"
MAX_AGE_DAYS = 30


def _key(case_id: str, user_message: str, client_id: str) -> str:
    payload = json.dumps({
        "case_id": case_id,
        "msg": user_message,
        "client_id": client_id,
    }, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def load_cache(path: Optional[Path] = None) -> dict:
    """Return {key: {reply, intent, priority, sentiment, ts}}."""
    path = path or CACHE_PATH
    if not path.exists():
        return {}
    out = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = entry.get("key")
            if not k:
                continue
            # Age gate
            age_days = (time.time() - (entry.get("ts") or 0)) / 86400
            if age_days > MAX_AGE_DAYS:
                continue
            out[k] = entry
    except OSError:
        return {}
    return out


def store(key: str, reply: str, intent: str, priority: str,
          sentiment: str = "neutral",
          path: Optional[Path] = None) -> None:
    path = path or CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "key": key, "ts": int(time.time()),
        "reply": reply, "intent": intent,
        "priority": priority, "sentiment": sentiment,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def clear(path: Optional[Path] = None) -> int:
    """Delete the cache file. Returns the number of lines removed."""
    path = path or CACHE_PATH
    if not path.exists():
        return 0
    try:
        n = len(path.read_text(encoding="utf-8").splitlines())
    except OSError:
        n = 0
    try:
        path.unlink()
    except OSError:
        pass
    return n


class CachingChatFn:
    """Wraps a chat_fn (signature matching llm.chat: caller, msg, conv,
    client=None) with a (case_id → key → cached response) layer.

    Usage:
        wrapped = CachingChatFn(llm.chat, case_id="sep1_overflow")
        result = wrapped(caller, msg, conv, client=client)
    """

    def __init__(self, inner, case_id: str,
                 cache_path: Optional[Path] = None, use_cache: bool = True):
        self._inner = inner
        self._case_id = case_id
        self._cache_path = cache_path or CACHE_PATH
        self._use_cache = use_cache
        self._memo = load_cache(self._cache_path) if use_cache else {}
        self.last_hit = False  # for introspection by the runner

    def __call__(self, caller, msg, conv=None, client=None):
        from types import SimpleNamespace
        client_id = (client or {}).get("id") or ""
        key = _key(self._case_id, msg, client_id)

        if self._use_cache and key in self._memo:
            e = self._memo[key]
            self.last_hit = True
            log.debug("eval cache HIT case=%s key=%s", self._case_id, key)
            return SimpleNamespace(
                reply=e["reply"], intent=e["intent"],
                priority=e["priority"],
                sentiment=e.get("sentiment", "neutral"),
            )

        self.last_hit = False
        result = self._inner(caller, msg, conv, client=client)
        # Persist to cache
        if self._use_cache:
            try:
                store(
                    key,
                    reply=getattr(result, "reply", "") or "",
                    intent=getattr(result, "intent", "General") or "General",
                    priority=getattr(result, "priority", "low") or "low",
                    sentiment=getattr(result, "sentiment", "neutral") or "neutral",
                    path=self._cache_path,
                )
                # Update local memo so the same process re-hits within the run
                self._memo[key] = {
                    "key": key, "ts": int(time.time()),
                    "reply": getattr(result, "reply", ""),
                    "intent": getattr(result, "intent", "General"),
                    "priority": getattr(result, "priority", "low"),
                    "sentiment": getattr(result, "sentiment", "neutral"),
                }
            except Exception as e:
                log.warning("eval cache store failed: %s", e)
        return result
