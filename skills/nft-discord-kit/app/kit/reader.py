"""Read holdings for linked wallets: EVM balances and special tokens in one Multicall3 round per RPC
endpoint, Solana collections through DAS. Also asks contract wallets about EIP-1271 signatures.
"""
from __future__ import annotations

import logging
import time
from typing import Mapping, Sequence

import aiohttp

from . import evm, solana
from .config import Collection, Settings, Special
from .holdings import Holdings
from .signatures import eip191_hash, is_magic, is_valid_signature_call

log = logging.getLogger("kit.reader")

Owners = Mapping[str, Sequence[tuple[str, str]]]  # user id -> [(kind, address)]


class HoldingsReader:
    def __init__(self, settings: Settings, session: aiohttp.ClientSession):
        self.s, self.session = settings, session
        self._rpcs: dict[str, evm.Rpc] = {}

    def rpc(self, env_name: str) -> evm.Rpc:
        if env_name not in self._rpcs:
            self._rpcs[env_name] = evm.Rpc(self.session, self.s.rpc_urls(env_name))
        return self._rpcs[env_name]

    async def read(self, owners: Owners) -> dict[str, Holdings]:
        """One reading per user. Parts the node did not answer are marked unknown, never zero."""
        wallets = {uid: [(k, a) for k, a in ws] for uid, ws in owners.items()}
        evm_w = {uid: [a for k, a in ws if k == "evm"] for uid, ws in wallets.items()}
        counts: dict[str, dict[str, int]] = {uid: {} for uid in wallets}
        specials: dict[str, dict[str, int]] = {uid: {} for uid in wallets}
        failed: set[str] = set()
        known: set[str] = set()

        groups: dict[tuple[str, str], list[Collection]] = {}
        for col in self.s.collections:
            if col.is_evm:
                groups.setdefault((col.rpc_env, col.multicall), []).append(col)
        any_evm = any(evm_w.values())
        for (env_name, multicall), cols in groups.items():
            group_specials = [sp for sp in self.s.specials if sp.collection in {c.name for c in cols}]
            if not any_evm:  # nobody here has an EVM wallet: nothing to hold, nothing to ask
                known |= {sp.name for sp in group_specials}
                continue
            calls, slots = self._evm_calls(cols, group_specials, evm_w)
            try:
                results = await evm.batch_call(self.rpc(env_name), calls, multicall)
            except evm.RpcError as e:
                log.warning("holdings via %s skipped: %s", env_name, e)
                failed |= {uid for uid, ws in evm_w.items() if ws}
                continue
            owner_of = {a.lower(): uid for uid, ws in evm_w.items() for a in ws}
            for (what, key, name), data in zip(slots, results, strict=True):
                if what == "count":
                    if data is None:  # balanceOf reverted: this user's total cannot be trusted
                        failed.add(key)
                    else:
                        counts[key][name] = counts[key].get(name, 0) + evm.as_int(data)
                elif what == "owner":  # ownerOf: None means burned or never minted
                    uid = owner_of.get("0x" + data[-40:].lower()) if data and len(data) >= 40 else None
                    if uid:
                        specials[uid][name] = specials[uid].get(name, 0) + 1
                elif data:  # ERC-1155 special: balanceOf(wallet, id)
                    specials[key][name] = specials[key].get(name, 0) + evm.as_int(data)
            known |= {sp.name for sp in group_specials}

        await self._read_solana(wallets, counts, failed)
        now = int(time.time())
        names = [c.name for c in self.s.collections]
        return {uid: Holdings({n: counts[uid].get(n, 0) for n in names},
                              {n: v for n, v in specials[uid].items() if v > 0}, uid not in failed,
                              frozenset(known), now)
                for uid in wallets}

    @staticmethod
    def _evm_calls(cols: Sequence[Collection], specials: Sequence[Special], evm_w: Mapping[str, list[str]]
                   ) -> tuple[list[evm.Call], list[tuple[str, str, str]]]:
        """Every read for one RPC endpoint, plus a slot per call saying what its answer means."""
        calls: list[evm.Call] = []
        slots: list[tuple[str, str, str]] = []  # (what, user id or special name, collection or special name)
        for col in cols:
            for uid, ws in evm_w.items():
                for w in ws:
                    if col.standard == "erc721":
                        calls.append((col.contract, evm.balance_of(w)))
                        slots.append(("count", uid, col.name))
                    else:
                        for tid in col.token_ids:
                            calls.append((col.contract, evm.balance_of_id(w, tid)))
                            slots.append(("count", uid, col.name))
        by_name = {c.name: c for c in cols}
        for sp in specials:
            col = by_name[sp.collection]
            if col.standard == "erc721":
                for tid in sp.token_ids:
                    calls.append((col.contract, evm.owner_of(tid)))
                    slots.append(("owner", sp.name, sp.name))
            else:
                for uid, ws in evm_w.items():
                    for w in ws:
                        for tid in sp.token_ids:
                            calls.append((col.contract, evm.balance_of_id(w, tid)))
                            slots.append(("special1155", uid, sp.name))
        return calls, slots

    async def _read_solana(self, wallets: Mapping[str, list[tuple[str, str]]], counts: dict[str, dict[str, int]],
                           failed: set[str]) -> None:
        cols = [c for c in self.s.collections if not c.is_evm]
        for uid, ws in wallets.items():
            for kind, address in ws:
                if kind != "solana":
                    continue
                for env_name in sorted({c.rpc_env for c in cols}):
                    group = [c for c in cols if c.rpc_env == env_name]
                    try:
                        held = await solana.collection_counts(self.rpc(env_name), address, {c.contract for c in group})
                    except evm.RpcError as e:
                        log.warning("solana holdings via %s skipped: %s", env_name, e)
                        failed.add(uid)
                        continue
                    for c in group:
                        counts[uid][c.name] = counts[uid].get(c.name, 0) + held.get(c.contract, 0)

    async def contract_signature_ok(self, address: str, message: str, signature: bytes) -> bool:
        """EIP-1271: ask a contract wallet (on any configured EVM chain where it has code) to vouch."""
        data = is_valid_signature_call(eip191_hash(message), signature)
        for env_name in dict.fromkeys(c.rpc_env for c in self.s.collections if c.is_evm):
            rpc = self.rpc(env_name)
            try:
                if not await rpc.has_code(address):
                    continue
                return is_magic(await rpc.eth_call(address, data))
            except (evm.RpcError, evm.Reverted) as e:
                log.info("EIP-1271 check via %s: %s", env_name, e)
        return False
