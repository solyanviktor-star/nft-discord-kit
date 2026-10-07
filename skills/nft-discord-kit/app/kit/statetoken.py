"""Signed, expiring tokens: the personal verify-link state, the website session and the OAuth state.

Format: base64url(JSON) + "." + hex(HMAC-SHA256(secret, base64url part))[:32]. The JSON carries a
purpose field `p`, so a token minted for one use (say a session) is refused for another.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Any

VERIFY = "verify"  # the link the bot hands out (30 minutes)
SESSION = "session"  # the website login cookie after Discord OAuth
OAUTH = "oauth"  # the short-lived OAuth `state` cookie


class TokenError(ValueError):
    """`str(error)` is "bad" (forged, damaged or wrong purpose) or "expired"."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(secret: bytes, body: str) -> str:
    return hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()[:32]


def sign(purpose: str, data: dict[str, Any], secret: bytes, ttl: int, now: float | None = None) -> str:
    """A token for `purpose` holding `data`, valid for `ttl` seconds."""
    payload = {**data, "p": purpose, "exp": int((time.time() if now is None else now) + ttl)}
    body = _b64encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"{body}.{_mac(secret, body)}"


def verify(purpose: str, token: str, secret: bytes, now: float | None = None) -> dict[str, Any]:
    """The payload of a valid token; raises TokenError("bad" | "expired")."""
    try:
        body, mac = str(token or "").strip().split(".", 1)
        if not hmac.compare_digest(mac.encode("ascii"), _mac(secret, body).encode("ascii")):
            raise TokenError("bad")
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except (ValueError, UnicodeError, binascii.Error):
        raise TokenError("bad") from None
    if not isinstance(payload, dict) or payload.get("p") != purpose:
        raise TokenError("bad")
    if float(payload.get("exp") or 0) < (time.time() if now is None else now):
        raise TokenError("expired")
    return payload
