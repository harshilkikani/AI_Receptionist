"""Claude wrapper: one structured call returns {reply, intent, priority}.

Same function used by web chat, voice, and SMS. Prompt is loaded from
prompts/receptionist_core.md and rendered per client/call.
"""

from pathlib import Path
from typing import Literal, Optional

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()
_anthropic = anthropic.Anthropic()
MODEL = "claude-haiku-4-5"

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


def _render_system_prompt(caller: Optional[dict], client: Optional[dict],
                          wrap_up_mode: Optional[str] = None) -> str:
    """Render the system prompt with tenant slots + caller memory.

    wrap_up_mode: None (normal), 'soft' (3:00), 'hard' (3:45) — injects
    extra wrap-up instruction so the AI starts closing out.
    """
    if client is None:
        from src import tenant as _t
        client = _t.load_default()

    template = _load_template()
    rendered = template
    substitutions = {
        "{{company_name}}": client.get("name", "this service"),
        "{{owner_name}}": client.get("owner_name", "the owner"),
        "{{services}}": client.get("services", "our services"),
        "{{pricing_summary}}": client.get("pricing_summary", "varies"),
        "{{service_area}}": client.get("service_area", "our area"),
        "{{hours}}": client.get("hours", "business hours"),
        "{{escalation_phone}}": client.get("escalation_phone", ""),
        "{{emergency_keywords}}": "/".join(client.get("emergency_keywords") or []),
        "{{memory}}": _format_memory(caller),
    }
    for k, v in substitutions.items():
        rendered = rendered.replace(k, str(v))

    if wrap_up_mode == "soft":
        rendered += (
            "\n\n[SYSTEM: Call has been going ~3 minutes. Start wrapping up. "
            "Make sure you have name + address + issue. Next reply should "
            "start closing the call with a callback commitment.]"
        )
    elif wrap_up_mode == "hard":
        rendered += (
            "\n\n[SYSTEM: Call duration hard limit approaching. Your next "
            "reply MUST be the final wrap-up. End with: "
            '"Okay — ' + client.get("owner_name", "the owner") +
            ' will call you back within the hour." Then stop.]'
        )
    return rendered


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
    system = _render_system_prompt(caller, client, wrap_up_mode=wrap_up_mode)
    messages = _build_messages(conversation or [], user_message)
    response = _anthropic.beta.messages.parse(
        model=MODEL,
        max_tokens=80,
        system=system,
        messages=messages,
        output_format=ChatResponse,
    )
    return response.parsed_output or _FALLBACK


def recover(caller: Optional[dict],
            client: Optional[dict] = None) -> ChatResponse:
    system = _render_system_prompt(caller, client) + (
        "\n\n[SYSTEM: This is a missed-call callback via SMS. Open casual, "
        "one sentence, trail off so they reply.]"
    )
    messages = [{"role": "user", "content": "(generate missed-call opening)"}]
    response = _anthropic.beta.messages.parse(
        model=MODEL,
        max_tokens=80,
        system=system,
        messages=messages,
        output_format=ChatResponse,
    )
    return response.parsed_output or _FALLBACK


# ── Token accounting hook (used by Section E usage tracker) ────────────

def last_token_usage(response) -> tuple:
    """Returns (input_tokens, output_tokens) from a parse() response, if
    the Anthropic SDK populates them on the object. Returns (0, 0) as
    a safe default if unavailable — usage tracking tolerates zeros."""
    usage = getattr(response, "usage", None)
    if not usage:
        return (0, 0)
    return (
        getattr(usage, "input_tokens", 0) or 0,
        getattr(usage, "output_tokens", 0) or 0,
    )
