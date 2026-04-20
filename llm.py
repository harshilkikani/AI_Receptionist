"""Claude wrapper: one structured call returns {reply, intent, priority}.

Same function is used by web chat, voice, and SMS — the transport is
different, the brain is identical.
"""

from typing import Literal, Optional
import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()
_anthropic = anthropic.Anthropic()
MODEL = "claude-haiku-4-5"


class ChatResponse(BaseModel):
    reply: str
    intent: Literal["Emergency", "Scheduling", "Quote", "Follow-up", "General"]
    priority: Literal["low", "high"]


SYSTEM_TEMPLATE = """You're Joanna, receptionist at {company_name}. Real phone call.

ONE sentence only. Stop after your first thought. 5-12 words. Never two sentences.
Vary openings. Casual, mid-thought, contractions. Never sound like a bot.
Stay on whatever topic the caller is on. Match their language. JSON fields in English.
Emergency ({emergency_keywords}): direct + priority="high".

CALLER:
{memory}
"""


def _render_system_prompt(caller: Optional[dict], client: Optional[dict]) -> str:
    """Fill the system prompt with tenant-specific slots and caller memory.
    In Section B this will be replaced with a file-based template renderer."""
    if client is None:
        # Late import to avoid circular dep (llm is imported by main which imports tenant)
        from src import tenant as _t
        client = _t.load_default()
    return SYSTEM_TEMPLATE.format(
        company_name=client.get("name", "this service"),
        emergency_keywords="/".join(client.get("emergency_keywords") or []),
        memory=_format_memory(caller),
    )

RECOVER_ADDENDUM = "\nCalling them back after missed call. Open casual, one sentence, trail off."


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


def chat(caller: Optional[dict], user_message: str,
         conversation: Optional[list] = None,
         client: Optional[dict] = None) -> ChatResponse:
    """client: tenant config dict; if None, uses default tenant at render time."""
    system = _render_system_prompt(caller, client)
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
    system = _render_system_prompt(caller, client) + RECOVER_ADDENDUM
    messages = [{"role": "user", "content": "(generate missed-call opening)"}]
    response = _anthropic.beta.messages.parse(
        model=MODEL,
        max_tokens=80,
        system=system,
        messages=messages,
        output_format=ChatResponse,
    )
    return response.parsed_output or _FALLBACK
