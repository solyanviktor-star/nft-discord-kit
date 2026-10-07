"""A fake JSON-RPC node: ERC-721/1155 balances, ownerOf, Multicall3 aggregate3, EIP-1271 and Solana DAS.

The ABI helpers here are the mirror image of kit.evm (they decode the calls and encode the answers),
so a round trip through them checks the production encoder and decoder against an independent one.
"""
from __future__ import annotations

from typing import Any

from aiohttp import web

from kit.config import MULTICALL3
from kit.evm import SEL_AGGREGATE3, SEL_BALANCE_OF, SEL_BALANCE_OF_ID, SEL_OWNER_OF
from kit.signatures import ERC1271_MAGIC


def word(n: int) -> str:
    return "%064x" % n


def pad(data: str) -> str:
    return data + "0" * (-len(data) % 64)


def decode_aggregate3_calls(calldata: str) -> list[tuple[str, str]]:
    h = calldata.removeprefix("0x")
    assert h[:8] == SEL_AGGREGATE3
    h = h[8:]

    def w(k: int) -> int:
        return int(h[k * 64:(k + 1) * 64], 16)

    head = w(0) // 32
    out = []
    for k in range(w(head)):
        start = head + 1 + w(head + 1 + k) // 32
        target = "0x" + h[start * 64 + 24:(start + 1) * 64]
        assert w(start + 1) == 1  # allowFailure
        data = start + w(start + 2) // 32
        out.append((target, h[(data + 1) * 64:(data + 1) * 64 + w(data) * 2]))
    return out


def encode_aggregate3_results(results: list[tuple[bool, str]]) -> str:
    tuples = [word(int(ok)) + word(0x40) + word(len(data) // 2) + pad(data) for ok, data in results]
    offsets, at = [], 32 * len(tuples)
    for t in tuples:
        offsets.append(at)
        at += len(t) // 2
    return "0x" + word(0x20) + word(len(tuples)) + "".join(word(o) for o in offsets) + "".join(tuples)


class Reverted(Exception):
    pass


class FakeNode:
    def __init__(self) -> None:
        self.balances: dict[str, dict[str, int]] = {}  # contract -> owner -> count (ERC-721)
        self.balances_1155: dict[str, dict[tuple[str, int], int]] = {}  # contract -> (owner, id) -> count
        self.owners: dict[str, dict[int, str]] = {}  # contract -> token id -> owner (missing: revert)
        self.code: dict[str, str] = {}  # address -> bytecode
        self.valid_1271: set[str] = set()  # contract wallets that accept any signature
        self.deployed: set[str] | None = None  # when set: calls to other addresses return empty data, like a chain
        self.multicall = True
        self.down = False
        self.status = 200
        self.assets: dict[str, list[dict[str, Any]]] = {}  # Solana owner -> DAS items
        self.requests: list[str] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/", self.handle)
        return app

    async def handle(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(body["method"])
        if self.down:
            return web.Response(status=self.status, text="down")
        try:
            result = self.answer(body["method"], body["params"])
        except Reverted:
            return web.json_response({"jsonrpc": "2.0", "id": 1, "error": {"code": 3, "message": "execution reverted"}})
        return web.json_response({"jsonrpc": "2.0", "id": 1, "result": result})

    def answer(self, method: str, params: Any) -> Any:
        if method == "eth_getCode":
            return self.code.get(params[0].lower(), "0x")
        if method == "getAssetsByOwner":
            items = self.assets.get(params["ownerAddress"], [])
            start = (params["page"] - 1) * params["limit"]
            return {"items": items[start:start + params["limit"]]}
        to, data = params[0]["to"].lower(), params[0]["data"]
        if to == MULTICALL3.lower():
            if not self.multicall:
                return "0x"  # nothing deployed there: an empty answer
            out = []
            for target, calldata in decode_aggregate3_calls(data):
                try:
                    out.append((True, self.call(target, calldata)))
                except Reverted:
                    out.append((False, ""))
            return encode_aggregate3_results(out)
        return "0x" + self.call(to, data.removeprefix("0x"))

    def call(self, target: str, data: str) -> str:
        if self.deployed is not None and target not in self.deployed:
            return ""  # nothing lives there: the EVM returns empty data, not an error
        sel, args = data[:8], data[8:]
        if sel == SEL_BALANCE_OF:
            return word(self.balances.get(target, {}).get("0x" + args[24:64], 0))
        if sel == SEL_BALANCE_OF_ID:
            return word(self.balances_1155.get(target, {}).get(("0x" + args[24:64], int(args[64:128], 16)), 0))
        if sel == SEL_OWNER_OF:
            owner = self.owners.get(target, {}).get(int(args[:64], 16))
            if owner is None:
                raise Reverted
            return word(int(owner, 16))
        if sel == ERC1271_MAGIC and target in self.valid_1271:
            return pad(ERC1271_MAGIC)
        raise Reverted
