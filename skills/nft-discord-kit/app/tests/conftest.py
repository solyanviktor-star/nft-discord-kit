"""Shared fixtures: a settings factory, a fake chain reader and a fake Discord server for the bot layer."""
from __future__ import annotations

import copy
import pathlib
import sys
from typing import Any

import pytest

APP = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from kit.config import Settings, parse_settings  # noqa: E402
from kit.holdings import Holdings  # noqa: E402

COLLECTION = "0x" + "11" * 20
BASE_CONFIG: dict[str, Any] = {
    "project": {"name": "Test Project", "color": "#5865F2", "public_url": "https://verify.example.com"},
    "discord": {"guild_id": 1000, "staff_roles": ["Team", "Mod"], "raffle_manager_roles": ["Raffle Manager"],
                "raffle_users": ["4242"]},
    "verification": {"sync_minutes": 30, "solana": True},
    "collections": [{"name": "Genesis", "chain": "ethereum", "rpc_env": "RPC_TEST", "contract": COLLECTION,
                     "standard": "erc721"}],
    "tiers": [{"name": "Holder", "min": 1}, {"name": "Collector", "min": 3}, {"name": "Whale", "min": 10}],
    "special": [{"name": "Legendary", "role": "Legendary", "collection": "Genesis", "token_ids": [7, 8],
                 "bonus_tickets": 3}],
    "raffle_chains": [{"name": "Ethereum", "wallet_kind": "evm"}, {"name": "Solana", "wallet_kind": "solana"},
                      {"name": "Bitcoin", "wallet_kind": "ordinals"}],
    "wallet_kinds": [{"id": "ordinals", "label": "Bitcoin", "fields": [
        {"key": "taproot", "label": "Taproot address", "pattern": "^bc1p[0-9a-z]{58}$"},
        {"key": "payment", "label": "Payment address", "pattern": "^(bc1|[13])[0-9A-Za-z]{25,62}$"}]}],
}
ENV = {"DISCORD_TOKEN": "test-token", "RPC_TEST": "http://localhost:9"}


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def b58encode(raw: bytes) -> str:
    """Base58 (the Solana address alphabet), for test keys."""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    num, out = int.from_bytes(raw, "big"), ""
    while num:
        num, rem = divmod(num, 58)
        out = alphabet[rem] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def make_settings(env: dict[str, str] | None = None, **over: Any) -> Settings:
    return parse_settings(merge(BASE_CONFIG, over), {**ENV, **(env or {})})


class FakeReader:
    """Holdings from plain dicts: address -> NFT count, address -> special set names held.

    `down = True` behaves like a dead node: every user with wallets comes back unreadable.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.specials: dict[str, set[str]] = {}
        self.down = False
        self.calls: list[dict[str, list[tuple[str, str]]]] = []
        self.contract_wallets: set[str] = set()

    async def read(self, owners):
        self.calls.append({uid: list(ws) for uid, ws in owners.items()})
        out = {}
        for uid, ws in owners.items():
            if self.down and ws:
                out[uid] = Holdings({}, {}, False, frozenset(), 1)
                continue
            special: dict[str, int] = {}
            for _, address in ws:
                for name in self.specials.get(address, ()):
                    special[name] = special.get(name, 0) + 1
            out[uid] = Holdings({"Genesis": sum(self.counts.get(a, 0) for _, a in ws)}, special, True,
                                frozenset({"Legendary"}), 1)
        return out

    async def contract_signature_ok(self, address: str, message: str, signature: bytes) -> bool:
        return address in self.contract_wallets


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def reader() -> FakeReader:
    return FakeReader()
