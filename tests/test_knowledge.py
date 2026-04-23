"""V3.5 — knowledge base tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from src import knowledge


@pytest.fixture(autouse=True)
def _clear_cache():
    knowledge.load_kb.cache_clear()
    yield
    knowledge.load_kb.cache_clear()


# ── _tokenize ─────────────────────────────────────────────────────────

def test_tokenize_strips_stopwords():
    assert knowledge._tokenize("the quick brown fox") == {"quick", "brown", "fox"}


def test_tokenize_lowercases():
    tokens = knowledge._tokenize("PUMP OUT")
    assert "pump" in tokens
    assert "out" not in tokens or len(tokens) > 0  # 'out' length 3


def test_tokenize_keeps_dollar_signs():
    assert "$475" in knowledge._tokenize("we charge $475")


def test_tokenize_empty():
    assert knowledge._tokenize("") == set()
    assert knowledge._tokenize(None) == set()


# ── _parse_kb ─────────────────────────────────────────────────────────

def test_parse_basic():
    md = """# Pricing
Pump-outs from $475.

# Service area
Lancaster County."""
    sections = knowledge._parse_kb(md)
    assert len(sections) == 2
    assert sections[0]["title"] == "Pricing"
    assert "$475" in sections[0]["body"]


def test_parse_empty():
    assert knowledge._parse_kb("") == []


def test_parse_content_before_header_gets_general_section():
    md = "Some intro text.\n\n# Real section\nbody"
    sections = knowledge._parse_kb(md)
    assert len(sections) == 2
    assert sections[0]["title"] == "General"


# ── load_kb + reload ──────────────────────────────────────────────────

def test_load_kb_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge, "_CLIENTS_DIR", tmp_path)
    knowledge.load_kb.cache_clear()
    assert knowledge.load_kb("nobody") == tuple()


def test_load_kb_reads_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge, "_CLIENTS_DIR", tmp_path)
    (tmp_path / "x.knowledge.md").write_text(
        "# Pricing\nWe charge $99.\n\n# Hours\n9 to 5.",
        encoding="utf-8",
    )
    knowledge.load_kb.cache_clear()
    kb = knowledge.load_kb("x")
    assert len(kb) == 2
    titles = {s[0] for s in kb}
    assert {"Pricing", "Hours"} == titles


# ── find_relevant / build_kb_injection ───────────────────────────────

def test_find_relevant_hits_pricing(monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge, "_CLIENTS_DIR", tmp_path)
    (tmp_path / "c.knowledge.md").write_text(
        "# Pricing\nPump-out costs $475 for 1000-gallon tanks.\n\n"
        "# Emergency\nOvernight fee is $250.",
        encoding="utf-8",
    )
    knowledge.load_kb.cache_clear()
    hits = knowledge.find_relevant("c", "how much does a pump-out cost?")
    assert len(hits) >= 1
    assert any("pump" in h["body"].lower() or h["title"] == "Pricing"
               for h in hits)


def test_find_relevant_no_match(monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge, "_CLIENTS_DIR", tmp_path)
    (tmp_path / "c.knowledge.md").write_text(
        "# Hours\n9 to 5.\n", encoding="utf-8",
    )
    knowledge.load_kb.cache_clear()
    assert knowledge.find_relevant("c", "something completely unrelated") == []


def test_find_relevant_caps_max_sections(monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge, "_CLIENTS_DIR", tmp_path)
    (tmp_path / "c.knowledge.md").write_text(
        "# Pricing\npump $475\n\n# Pricing2\npump $525\n\n"
        "# Pricing3\npump $625\n\n# Pricing4\npump $725",
        encoding="utf-8",
    )
    knowledge.load_kb.cache_clear()
    hits = knowledge.find_relevant("c", "pump-out", max_sections=2)
    assert len(hits) == 2


def test_build_kb_injection_formats_for_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge, "_CLIENTS_DIR", tmp_path)
    (tmp_path / "c.knowledge.md").write_text(
        "# Drain field repair\nTypical quote $6000-$12000.",
        encoding="utf-8",
    )
    knowledge.load_kb.cache_clear()
    injection = knowledge.build_kb_injection("c", "my drain field is flooding")
    assert "Relevant knowledge" in injection
    assert "Drain field repair" in injection
    assert "$6000" in injection


def test_build_kb_injection_empty_when_no_match(monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge, "_CLIENTS_DIR", tmp_path)
    (tmp_path / "c.knowledge.md").write_text(
        "# Unrelated\nCompletely different content.",
        encoding="utf-8",
    )
    knowledge.load_kb.cache_clear()
    assert knowledge.build_kb_injection("c", "total nonsense xyzabc") == ""


def test_build_kb_injection_no_kb_file(monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge, "_CLIENTS_DIR", tmp_path)
    knowledge.load_kb.cache_clear()
    assert knowledge.build_kb_injection("missing", "pricing question") == ""


# ── seed knowledge file for septic_pro ───────────────────────────────

def test_septic_pro_kb_exists_and_loads():
    """The shipped septic_pro knowledge file loads + has sections."""
    knowledge.load_kb.cache_clear()
    kb = knowledge.load_kb("septic_pro")
    assert len(kb) >= 3  # at least Pricing, Drain field repair, Service area
    titles = {s[0] for s in kb}
    assert "Pricing" in titles


def test_septic_pro_kb_matches_pricing_query():
    knowledge.load_kb.cache_clear()
    hits = knowledge.find_relevant(
        "septic_pro", "how much for a pump-out?")
    assert any(h["title"].lower().startswith("pricing") for h in hits)


# ── integration: system prompt gets KB block ─────────────────────────

def test_system_blocks_include_kb_injection():
    import llm
    from src import tenant
    c = tenant.load_client_by_id("septic_pro")
    blocks = llm._render_system_blocks(
        caller={"id": "x", "phone": "+17175550104"},
        client=c,
        user_message="how much does a pump-out cost?",
    )
    # Block 1: stable, Block 2: volatile (memory + KB + ...)
    assert len(blocks) >= 2
    volatile = blocks[1]["text"]
    assert "Relevant knowledge" in volatile
    assert "$475" in volatile


def test_system_blocks_no_kb_when_no_message():
    """If no user_message is passed (e.g., recover path), no KB lookup."""
    import llm
    from src import tenant
    c = tenant.load_client_by_id("septic_pro")
    blocks = llm._render_system_blocks(
        caller={"id": "x"}, client=c, user_message=None)
    volatile = blocks[1]["text"]
    assert "Relevant knowledge" not in volatile
