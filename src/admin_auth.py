"""V5.2 — Shared admin Basic-auth helper.

Extracted from src/admin.py so other admin-prefixed routers (notably
src/recordings.py for /admin/recording/{sid}.mp3) can reuse the SAME
dependency instead of duplicating logic or — worse — silently skipping
auth.

Behavior unchanged from the original `_check_auth`:
  - Returns None when ADMIN_USER or ADMIN_PASS is unset (local-only).
  - Returns 401 with WWW-Authenticate: Basic on missing creds.
  - Returns 401 with WWW-Authenticate: Basic on wrong creds.
  - Returns the username on success (so routes can log who acted).
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials


security = HTTPBasic(auto_error=False)


def auth_required() -> bool:
    return bool(os.environ.get("ADMIN_USER")) and bool(os.environ.get("ADMIN_PASS"))


def check_admin_auth(
    creds: Optional[HTTPBasicCredentials] = Depends(security),
):
    """FastAPI dependency. Use with `Depends(check_admin_auth)`."""
    if not auth_required():
        return None
    if not creds:
        raise HTTPException(
            status_code=401,
            detail="Admin auth required",
            headers={"WWW-Authenticate": "Basic"},
        )
    if (creds.username != os.environ.get("ADMIN_USER")
            or creds.password != os.environ.get("ADMIN_PASS")):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username
