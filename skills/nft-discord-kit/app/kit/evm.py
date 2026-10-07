"""EVM reads over JSON-RPC.

Calls are batched through Multicall3 `aggregate3` (allowFailure on every call, so one broken
call does not sink the batch) with a plain `eth_call` fallback for chains without Multicall3.
Every chain may list several RPC URLs; each one gets a few tries before the next is used.
RPC URLs often embed API keys, so only their host name is ever logged.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence
from urllib.parse import urlsplit

import aiohttp

log = logging.getLogger("kit.evm")

SEL_AGGREGATE3 = "82ad56cb"  # aggregate3((address,bool,bytes)[])
SEL_BALANCE_OF = "70a08231"  # balanceOf(address)
SEL_BALANCE_OF_ID = "00fdd58e"  # balanceOf(address,uint256), ERC-1155
SEL_OWNER_OF = "6352211e"  # ownerOf(uint256)

Call = tuple[str, str]  # (contract address, calldata hex without 0x)


class RpcError(Exception):
    """The node could not be reached or kept failing. Not a contract revert."""


class Reverted(Exception):
    """The call itself reverted, e.g. ownerOf for a burned token."""


def _word(n: int) -> str:
    return "%064x" % n


def _address_word(address: str) -> str:
    return address.lower().removeprefix("0x").rjust(64, "0")


def _pad(data: str) -> str:
    return data + "0" * (-len(data) % 64)


def balance_of(owner: str) -> str:
    return SEL_BALANCE_OF + _address_word(owner)


def balance_of_id(owner: str, token_id: int) -> str:
    return SEL_BALANCE_OF_ID + _address_word(owner) + _word(token_id)


def owner_of(token_id: int) -> str:
    return SEL_OWNER_OF + _word(token_id)


def encode_aggregate3(calls: Sequence[Call]) -> str:
    """Calldata for Multicall3.aggregate3 with allowFailure = true on every call."""
    structs = [_address_word(target) + _word(1) + _word(0x60) + _word(len(data) // 2) + _pad(data)
               for target, data in calls]
    offsets, at = [], 32 * len(structs)
    for struct in structs:
        offsets.append(at)
        at += len(struct) // 2
    return ("0x" + SEL_AGGREGATE3 + _word(0x20) + _word(len(calls))
            + "".join(_word(o) for o in offsets) + "".join(structs))


def decode_aggregate3(result: str, n: int) -> list[str | None]:
    """aggregate3 return data -> n return values as hex (no 0x); None where a call failed.

    Raises ValueError when the data is not an aggregate3 answer for n calls (e.g. "0x" from a
    chain where nothing lives at the Multicall3 address).
    """
    h = result.removeprefix("0x")

    def word(k: int) -> int:
        chunk = h[k * 64:(k + 1) * 64]
        if len(chunk) != 64:
            raise ValueError("truncated aggregate3 result")
        return int(chunk, 16)

    head = word(0) // 32  # index of the array length word
    if word(head) != n:
        raise ValueError("aggregate3 returned a different number of results")
    out: list[str | None] = []
    for k in range(n):
        start = head + 1 + word(head + 1 + k) // 32  # (bool success, bytes data)
        data = start + word(start + 1) // 32
        size = word(data)
        out.append(h[(data + 1) * 64:(data + 1) * 64 + size * 2] if word(start) else None)
    return out


def _is_revert(error: Any) -> bool:
    if not isinstance(error, dict):
        return False
    return error.get("code") == 3 or "revert" in str(error.get("message", "")).lower()


class Rpc:
    """JSON-RPC over HTTP with retries per URL and fallback to the next URL."""

    def __init__(self, session: aiohttp.ClientSession, urls: Sequence[str], tries: int = 2,
                 timeout: float = 30.0, backoff: float = 1.0):
        self.session, self.urls = session, list(urls)
        self.tries, self.timeout, self.backoff = tries, timeout, backoff

    async def request(self, method: str, params: Any) -> Any:
        if not self.urls:
            raise RpcError("no RPC URL configured")
        last = "no answer"
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        for url in self.urls:
            for attempt in range(self.tries):
                try:
                    async with self.session.post(url, json=body,
                                                 timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                        if resp.status != 200:
                            raise RpcError(f"HTTP {resp.status}")
                        answer = await resp.json(content_type=None)
                    if not isinstance(answer, dict):
                        raise RpcError("not a JSON-RPC answer")
                    if answer.get("error"):
                        if _is_revert(answer["error"]):
                            raise Reverted(str(answer["error"].get("message", ""))[:120])
                        raise RpcError(str(answer["error"])[:160])
                    if "result" not in answer:
                        raise RpcError("no result")
                    return answer["result"]
                except (RpcError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                    last = str(e) or type(e).__name__
                    log.warning("rpc %s %s try %d: %s", urlsplit(url).hostname, method, attempt + 1, last)
                    if attempt + 1 < self.tries:
                        await asyncio.sleep(self.backoff * (attempt + 1))
        raise RpcError(last)

    async def eth_call(self, to: str, data: str) -> str:
        data = data if data.startswith("0x") else "0x" + data
        return str(await self.request("eth_call", [{"to": to, "data": data}, "latest"]))

    async def has_code(self, address: str) -> bool:
        code = str(await self.request("eth_getCode", [address, "latest"]))
        return code not in ("", "0x", "0x0")


async def _plain_call(rpc: Rpc, call: Call, gate: asyncio.Semaphore) -> str | None:
    async with gate:
        try:
            return (await rpc.eth_call(*call)).removeprefix("0x")
        except Reverted:
            return None


async def batch_call(rpc: Rpc, calls: Sequence[Call], multicall: str, chunk: int = 100) -> list[str | None]:
    """Run read-only calls; returns each call's return data (hex, no 0x), None where it reverted.

    Raises RpcError when the node is down: the caller must treat that as "unknown", never as "zero".
    """
    out: list[str | None] = []
    gate = asyncio.Semaphore(8)
    for i in range(0, len(calls), chunk):
        part = calls[i:i + chunk]
        results = None
        if multicall:
            try:
                results = decode_aggregate3(await rpc.eth_call(multicall, encode_aggregate3(part)), len(part))
            except (Reverted, ValueError) as e:
                log.info("multicall unusable here (%s); using plain eth_call", e)
        if results is None:
            results = list(await asyncio.gather(*(_plain_call(rpc, c, gate) for c in part)))
        out.extend(results)
    return out


def as_int(data: str | None) -> int:
    """A uint256 return value; empty data counts as 0."""
    return int(data[:64], 16) if data else 0
