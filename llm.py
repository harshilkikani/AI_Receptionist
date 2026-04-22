"""Claude wrapper: one structured call returns {reply, intent, priority}.

Same function used by web chat, voice, and SMS. Prompt is loaded from
prompts/receptionist_core.md and rendered per client/call.
"""

import logging
from pathlib import Path
from typing import Literal, Optional

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()
_anthropic = anthropic.Anthropic()
MODEL = "claude-haiku-4-5"

_log = logging.getLogger("llm")

# P8 — running tally of cache behavior across this process. Useful for
# diagnostics; the admin UI or an operator one-liner can read it.
_cache_stats = {"reads": 0, "writes": 0, "total_input": 0,
                "total_cache_read": 0}


def cache_stats() -> dict:
    """Process-local cache-hit telemetry. `reads` = # of responses that
    carried a non-zero cache_read_input_tokens."""
    return dict(_cache_stats)


def reset_cache_stats():
    global _cache_stats
    _cache_stats = {"reads": 0, "writes": 0, "total_input": 0,
                    "total_cache_read": 0}

PROMPT_PATH = Path(__file__).parent / "prompts" / "receptionist_core.md"


class ChatResponse(BaseModel):
    reply: str
    intent: Literal["Emergency", "Scheduling", "Quote", "Follow-up", "General"]
    priority: Literal["low", "high"]


# ── Prompt loading + rendering ─────────────────────────────────────────

_cached_template: Optional[str] = None


def _load_template() -> str:
    """Read prompts/receptionist_core.md once per process."""
    global _cached_template
    if _cached_template is None:
        _cached_template = PROMPT_PATH.read_text(encoding="utf-8")
    return _cached_template


def reload_prompt():
    """Clear the template cache. Useful for tests + admin editing."""
    global _cached_template
    _cached_template = None


def _format_memory(caller: Optional[dict]) -> str:
    if not caller or caller.get("type") == "new" and not caller.get("history"):
        if not caller:
            return "No caller on file. New lead."
        return f"New lead. Phone: {caller.get('phone', 'unknown')}."

    lines = [
        f"Name: {caller.get('name', 'Unknown')}",
        f"Phone: {caller.get('phone', 'Unknown')}",
        f"Address: {caller.get('address') or 'Unknown'}",
        f"Equipment: {caller.get('equipment') or 'Unknown'}",
        f"Notes: {caller.get('notes') or 'None'}",
    ]
    for h in (caller.get("history") or [])[:3]:
        lines.append(f"  - {h.get('date', '?')}: {h.get('note', '')}")
    return "\n".join(lines)


def _cache_enabled() -> bool:
    """P8 — toggle via env. Default on. Set PROMPT_CACHE_ENABLED=false to
    A/B test or diagnose."""
    import os as _os
    return _os.environ.get("PROMPT_CACHE_ENABLED", "true").lower() != "false"


def _render_stable_text(client: Optional[dict]) -> str:
    """The tenant-scoped prompt body. Constant across calls for one
    tenant, so this is what we cache."""
    if client is None:
        from src import tenant as _t
        client = _t.load_default()
    template = _load_template()
    substitutions = {
        "{{company_name}}": client.get("name", "this service"),
        "{{owner_name}}": client.get("owner_name", "the owner"),
        "{{services}}": client.get("services", "our services"),
        "{{pricing_summary}}": client.get("pricing_summary", "varies"),
        "{{service_area}}": client.get("service_area", "our area"),
        "{{hours}}": client.get("hours", "business hours"),
        "{{escalation_phone}}": client.get("escalation_phone", ""),
        "{{emergency_keywords}}": "/".join(client.get("emergency_keywords") or []),
    }
    rendered = template
    for k, v in substitutions.items():
        rendered = rendered.replace(k, str(v))
    return rendered


def _wrap_up_suffix(wrap_up_mode: Optional[str], client: Optional[dict]) -> str:
    if wrap_up_mode == "soft":
        return (
            "\n\n[SYSTEM: Call has been going ~3 minutes. Start wrapping up. "
            "Make sure you have name + address + issue. Next reply should "
            "start closing the call with a callback commitment.]"
        )
    if wrap_up_mode == "hard":
        owner = (client or {}).get("owner_name", "the owner")
        return (
            "\n\n[SYSTEM: Call duration hard limit approaching. Your next "
            "reply MUST be the final wrap-up. End with: "
            f'"Okay — {owner} will call you back within the hour." Then stop.]'
        )
    return ""


def _render_system_blocks(caller: Optional[dict], client: Optional[dict],
                          wrap_up_mode: Optional[str] = None,
                          recover_suffix: Optional[str] = None) -> list:
    """Return the system-prompt blocks to pass to the Anthropic API.

    Block 1 (cacheable): the tenant-scoped prompt body. Same for every
    caller under one tenant. With cache_control=ephemeral the Anthropic
    router reuses this prefix for 5 minutes, saving ~90% on input cost
    for the prefix tokens.

    Block 2+ (not cached): caller-specific memory + optional wrap-up
    cue + optional recover suffix — all volatile.
    """
    stable = _render_stable_text(client)
    memory_text = "## Caller memory (injected per-call)\n" + _format_memory(caller)
    wrap_up = _wrap_up_suffix(wrap_up_mode, client)

    volatile_parts = [memory_text]
    if wrap_up:
        volatile_parts.append(wrap_up.lstrip())
    if recover_suffix:
        volatile_parts.append(recover_suffix.lstrip())
    volatile = "\n\n".join(volatile_parts)

    if _cache_enabled():
        return [
            {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": volatile},
        ]
    # Caching disabled — return a single plain block
    return [{"type": "text", "text": stable + "\n\n" + volatile}]


def _render_system_prompt(caller: Optional[dict], client: Optional[dict],
                          wrap_up_mode: Optional[str] = None) -> str:
    """Backwards-compatible flat string version of the system prompt.
    Used in tests and any caller that wants to inspect the whole prompt."""
    blocks = _render_system_blocks(caller, client, wrap_up_mode=wrap_up_mode)
    return "\n\n".join(b["text"] for b in blocks)


def _build_messages(conversation: list, new_user_message: str) -> list:
    msgs = []
    for turn in (conversation or [])[-4:]:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        msgs.append({"role": role, "content": turn.get("text", "")})
    msgs.append({"role": "user", "content": new_user_message})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


_FALLBACK = ChatResponse(
    reply="Gimme one sec, let me grab someone.",
    intent="General",
    priority="low",
)


# ── Public API ─────────────────────────────────────────────────────────

def chat(caller: Optional[dict], user_message: str,
         conversation: Optional[list] = None,
         client: Optional[dict] = None,
         wrap_up_mode: Optional[str] = None) -> ChatResponse:
    """Main LLM call. `client` is tenant config (dict). `wrap_up_mode` is
    set by the call timer when approaching duration caps."""
    reply, _usage = chat_with_usage(caller, user_message, conversation,
                                    client, wrap_up_mode)
    return reply


def chat_with_usage(caller: Optional[dict], user_message: str,
                    conversation: Optional[list] = None,
                    client: Optional[dict] = None,
                    wrap_up_mode: Optional[str] = None):
    """Same as chat(), but also returns (input_tokens, output_tokens)
    so callers can log usage. Use this in the voice/SMS request path.

    P8 — system prompt is now sent as cacheable blocks so repeated calls
    under the same tenant hit the Anthropic ephemeral cache. The first
    tuple element is a ChatResponse; the second is a plain tuple
    (input_tokens, output_tokens) for backwards compatibility."""
    system_blocks = _render_system_blocks(caller, client,
                                          wrap_up_mode=wrap_up_mode)
    messages = _build_messages(conversation or [], user_message)
    response = _anthropic.beta.messages.parse(
        model=MODEL,
        max_tokens=80,
        system=system_blocks,
        messages=messages,
        output_format=ChatResponse,
    )
    in_tok, out_tok, cache_read = last_token_usage(response)
    _cache_stats["total_input"] += in_tok
    _cache_stats["total_cache_read"] += cache_read
    if cache_read > 0:
        _cache_stats["reads"] += 1
        _log.info(
            "prompt_cache_hit cache_read=%d input=%d savings_tokens=%d",
            cache_read, in_tok, cache_read,
        )
    else:
        _cache_stats["writes"] += 1
    return (response.parsed_output or _FALLBACK), (in_tok, out_tok)


def recover(caller: Optional[dict],
            client: Optional[dict] = None) -> ChatResponse:
    system_blocks = _render_system_blocks(
        caller, client,
        recover_suffix=(
            "[SYSTEM: This is a missed-call callback via SMS. Open casual, "
            "one sentence, trail off so they reply.]"
        ),
    )
    messages = [{"role": "user", "content": "(generate missed-call opening)"}]
    response = _anthropic.beta.messages.parse(
        model=MODEL,
        max_tokens=80,
        system=system_blocks,
        messages=messages,
        output_format=ChatResponse,
    )
    return response.parsed_output or _FALLBACK


# ── Token accounting hook (used by Section E usage tracker) ────────────

def last_token_usage(response) -> tuple:
    """Returns (input_tokens, output_tokens, cache_read_input_tokens)
    from a parse() response. Missing fields default to 0.

    P8 — cache_read_input_tokens lets `usage.log_turn` distinguish fresh
    input tokens from cache hits so the monthly cost calc doesn't
    over-charge the client for prefix tokens the API served from cache.
    """
    usage = getattr(response, "usage", None)
    if not usage:
        return (0, 0, 0)
    return (
        getattr(usage, "input_tokens", 0) or 0,
        getattr(usage, "output_tokens", 0) or 0,
        getattr(usage, "cache_read_input_tokens", 0) or 0,
    )
