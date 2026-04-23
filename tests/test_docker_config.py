"""V3.17 — Dockerfile + docker-compose syntax + content checks.

These don't actually run docker. They parse the files and assert
invariants so a bad paste doesn't ship unnoticed.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).parent.parent


def test_dockerfile_exists():
    assert (_ROOT / "Dockerfile").exists()


def test_dockerfile_exposes_port_8765():
    content = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "EXPOSE 8765" in content


def test_dockerfile_uses_slim_python():
    content = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    # Any slim Python 3.x base is acceptable — lock in the shape only
    assert re.search(r"FROM python:3\.\d+-slim", content)


def test_dockerfile_runs_as_non_root():
    content = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER app" in content or "USER 1000" in content


def test_dockerfile_has_healthcheck():
    content = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "HEALTHCHECK" in content
    assert "/health" in content


def test_dockerfile_uses_tini_or_exec():
    """Signal handling: either tini or exec form ENTRYPOINT."""
    content = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "tini" in content.lower() or 'ENTRYPOINT ["' in content


def test_dockerfile_copies_requirements_first():
    """Cache optimization: requirements.txt should be COPY'd before the
    full source so rebuilds after a code-only change skip pip install."""
    content = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    # Find positions of the two COPY commands
    req_idx = content.find("COPY requirements.txt")
    src_idx = content.find("COPY . .")
    assert req_idx >= 0
    assert src_idx >= 0
    assert req_idx < src_idx


def test_compose_file_parses():
    compose = yaml.safe_load(
        (_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert isinstance(compose, dict)
    assert "services" in compose
    assert "app" in compose["services"]


def test_compose_port_mapping():
    compose = yaml.safe_load(
        (_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    ports = compose["services"]["app"].get("ports") or []
    # Expect 8765:8765 somewhere
    joined = " ".join(str(p) for p in ports)
    assert "8765:8765" in joined


def test_compose_mounts_data_volume():
    compose = yaml.safe_load(
        (_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    volumes = compose["services"]["app"].get("volumes") or []
    joined = " ".join(str(v) for v in volumes)
    assert "./data:/app/data" in joined
    assert "./clients:/app/clients" in joined


def test_dockerignore_excludes_sensitive():
    content = (_ROOT / ".dockerignore").read_text(encoding="utf-8")
    # Must exclude .env and git state
    assert ".env" in content
    assert ".git" in content
    # Must exclude data/ and logs/ (they're host-mounted)
    assert "data/" in content
    assert "logs/" in content


def test_dockerignore_excludes_tests():
    """Tests shouldn't bloat the image."""
    content = (_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "tests/" in content
