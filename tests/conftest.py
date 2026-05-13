"""Shared fixtures — make sure every test runs in isolation."""

import os
import sys
from pathlib import Path

# Ensure repo root on sys.path so `from src import ...` works
_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pytest

# Provide a dummy ANTHROPIC_API_KEY so llm can import without network
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-placeholder")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Each test gets a clean environment and a clean SQLite DB."""
    # Reset feature flags to defaults (safe baseline)
    for key in [
        "MARGIN_PROTECTION_ENABLED",
        "ENFORCE_CALL_DURATION_CAP",
        "ENFORCE_SPAM_FILTER",
        "ENFORCE_SMS_CAP",
        "ENFORCE_USAGE_ALERTS",
    ]:
        monkeypatch.delenv(key, raising=False)

    # Redirect the usage DB to a temp location
    from src import usage
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage_test.db")
    # V5.9 — _init_schema is now one-shot per process; reset the flag so
    # the first call against this tmp DB actually creates the tables.
    usage._reset_schema_cache()

    # V3.16 — tests should never accidentally hit the on-disk eval cache.
    # EVAL_CACHE_DISABLE=true forces runner.run_case to bypass it.
    # The dedicated cache tests explicitly override this via their own
    # fixtures where they need to test caching behavior.
    monkeypatch.setenv("EVAL_CACHE_DISABLE", "true")

    # Clear any cached state in modules we touch
    from src import tenant
    tenant.reload()

    # V5.9 — recall block has a 30s TTL cache; tests recreate the DB per
    # case so stale cached blocks would falsely pass.
    try:
        from src import recall
        recall.reset_recall_cache()
    except Exception:
        pass

    # V7.1 — neutralize the live-demo ElevenLabs config for unit tests.
    # When the developer has flipped clients/ace_hvac.yaml to
    # `tts_provider: elevenlabs` AND has ELEVENLABS_API_KEY set AND
    # data/tunnel_url.txt is on disk, every test that hits a voice
    # route would emit <Play> URLs instead of <Say>. Force ElevenLabs
    # OFF by default; the V4.1/V5.6-V5.8 tests that specifically want
    # it on set ELEVENLABS_API_KEY back in their own fixtures.
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    # Point the tunnel hint at a tmp path so the fallback resolves to
    # empty during tests (forces Polly path).
    try:
        from src import tts
        monkeypatch.setattr(tts, "_TUNNEL_HINT_FILE",
                            tmp_path / "tunnel_hint_unused.txt")
        tts.reset_base_url_cache()
    except Exception:
        pass

    yield


@pytest.fixture
def client_ace():
    from src import tenant
    return tenant.load_client_by_number("+18449403274")


@pytest.fixture
def client_default():
    from src import tenant
    return tenant.load_default()
