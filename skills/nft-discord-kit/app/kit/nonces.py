"""Single-use sign-in challenges that expire after 10 minutes.

Kept in memory on purpose: a restart only means "press Connect again", and nothing about a
pending challenge is worth a database row.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Callable

NONCE_TTL = 10 * 60
MAX_PENDING = 20_000


@dataclass(frozen=True)
class Challenge:
    nonce: str
    user_id: str
    user_name: str
    kind: str  # "evm" | "solana"
    address: str
    message: str
    expires: float


class NonceBook:
    def __init__(self, ttl: int = NONCE_TTL, clock: Callable[[], float] = time.time):
        self.ttl, self.clock = ttl, clock
        self._pending: dict[str, Challenge] = {}

    def issue(self, user_id: str, user_name: str, kind: str, address: str,
              make_message: Callable[[str, float, float], str]) -> Challenge:
        """A new challenge; `make_message(nonce, issued_at, expires_at)` writes the text to sign."""
        now = self.clock()
        self._prune(now)
        nonce = secrets.token_hex(12)
        challenge = Challenge(nonce, user_id, user_name, kind, address,
                              make_message(nonce, now, now + self.ttl), now + self.ttl)
        self._pending[nonce] = challenge
        return challenge

    def take(self, nonce: str) -> Challenge | None:
        """The challenge for `nonce`, removed so it cannot be used twice; None if unknown or expired."""
        challenge = self._pending.pop(str(nonce or ""), None)
        if challenge is None or challenge.expires < self.clock():
            return None
        return challenge

    def _prune(self, now: float) -> None:
        for nonce in [n for n, c in self._pending.items() if c.expires < now]:
            del self._pending[nonce]
        while len(self._pending) >= MAX_PENDING:  # oldest first: dicts keep insertion order
            del self._pending[next(iter(self._pending))]
