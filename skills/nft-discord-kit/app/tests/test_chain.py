"""Chain reads: the Multicall3 codec, and the holdings reader against a fake JSON-RPC node."""
from __future__ import annotations

import aiohttp
import pytest

from chainfake import FakeNode, decode_aggregate3_calls, encode_aggregate3_results
from conftest import b58encode, make_settings
from kit import evm
from kit.reader import HoldingsReader

COLLECTION = "0x" + "11" * 20
EDITIONS = "0x" + "22" * 20
ALICE = "0x" + "a1" * 20
BOB = "0x" + "b0" * 20
CAROL = "0x" + "c0" * 20


def test_aggregate3_round_trip():
    calls = [(COLLECTION, evm.balance_of(ALICE)), (EDITIONS, evm.balance_of_id(BOB, 5)), (COLLECTION, evm.owner_of(7))]
    assert decode_aggregate3_calls(evm.encode_aggregate3(calls)) == [(t.lower(), d) for t, d in calls]
    answers = [(True, "%064x" % 3), (False, ""), (True, "ab" * 40)]
    assert evm.decode_aggregate3(encode_aggregate3_results(answers), 3) == ["%064x" % 3, None, "ab" * 40]


def test_decode_rejects_non_multicall_answers():
    with pytest.raises(ValueError):
        evm.decode_aggregate3("0x", 1)
    with pytest.raises(ValueError):
        evm.decode_aggregate3(encode_aggregate3_results([(True, "00")]), 2)


@pytest.fixture
async def node(aiohttp_server):
    fake = FakeNode()
    server = await aiohttp_server(fake.app())
    fake.url = str(server.make_url("/"))
    return fake


def chain_settings(url: str, **over):
    over.setdefault("collections", [
        {"name": "Genesis", "chain": "ethereum", "rpc_env": "RPC_TEST", "contract": COLLECTION, "standard": "erc721"},
        {"name": "Editions", "chain": "ethereum", "rpc_env": "RPC_TEST", "contract": EDITIONS, "standard": "erc1155",
         "token_ids": [1, 2]}])
    over.setdefault("special", [{"name": "Legendary", "role": "Legendary", "collection": "Genesis",
                                 "token_ids": [7, 8, 9], "bonus_tickets": 3}])
    return make_settings(env={"RPC_TEST": url, "RPC_SOLANA": url}, **over)


async def read(settings, owners):
    async with aiohttp.ClientSession() as session:
        reader = HoldingsReader(settings, session)
        for rpc in [reader.rpc("RPC_TEST"), reader.rpc("RPC_SOLANA")]:
            rpc.backoff = 0
        return await reader.read(owners)


async def test_holdings_through_multicall(node):
    node.balances[COLLECTION] = {ALICE: 2, BOB: 1}
    node.balances_1155[EDITIONS] = {(ALICE, 1): 1, (ALICE, 2): 3}
    node.owners[COLLECTION] = {7: ALICE, 8: CAROL}  # token 9 is burned: ownerOf reverts
    h = await read(chain_settings(node.url), {"u1": [("evm", ALICE), ("evm", BOB)], "u2": [("evm", CAROL)], "u3": []})
    assert h["u1"].counts == {"Genesis": 3, "Editions": 4} and h["u1"].total == 7 and h["u1"].complete
    assert h["u1"].specials == {"Legendary": 1} and "Legendary" in h["u1"].specials_known
    assert h["u2"].total == 0 and h["u2"].specials == {"Legendary": 1}
    assert h["u3"].total == 0 and h["u3"].complete
    assert node.requests == ["eth_call"]  # one batch for everyone


async def test_falls_back_to_plain_eth_call_without_multicall(node):
    node.multicall = False
    node.balances[COLLECTION] = {ALICE: 2}
    node.owners[COLLECTION] = {8: ALICE}
    h = await read(chain_settings(node.url), {"u1": [("evm", ALICE)]})
    assert h["u1"].counts["Genesis"] == 2 and h["u1"].specials == {"Legendary": 1} and h["u1"].complete
    assert node.requests.count("eth_call") > 1


async def test_dead_node_means_unknown_not_zero(node):
    node.down, node.status = True, 429
    h = await read(chain_settings(node.url), {"u1": [("evm", ALICE)], "u2": []})
    assert not h["u1"].complete and not h["u1"].specials_known
    assert h["u2"].complete  # nobody to ask about: an empty account is simply empty


async def test_second_rpc_url_is_used_when_the_first_fails(node, aiohttp_server):
    from aiohttp import web
    broken = await aiohttp_server(web.Application())  # every POST answers 404
    node.balances[COLLECTION] = {ALICE: 5}
    s = chain_settings(f"{broken.make_url('/')},{node.url}")
    h = await read(s, {"u1": [("evm", ALICE)]})
    assert h["u1"].counts["Genesis"] == 5


async def test_solana_collections_through_das(node):
    collection, wallet = b58encode(bytes([12] * 32)), b58encode(bytes([13] * 32))
    good = {"grouping": [{"group_key": "collection", "group_value": collection}]}
    node.assets[wallet] = ([good] * 1000 + [good, {"grouping": [{"group_key": "collection", "group_value": collection,
                                                                  "verified": False}]},
                            {**good, "burnt": True}])
    s = chain_settings(node.url, collections=[
        {"name": "Genesis", "chain": "ethereum", "rpc_env": "RPC_TEST", "contract": COLLECTION, "standard": "erc721"},
        {"name": "Sol", "standard": "solana", "rpc_env": "RPC_SOLANA", "contract": collection}])
    h = await read(s, {"u1": [("solana", wallet)]})
    assert h["u1"].counts == {"Genesis": 0, "Sol": 1001} and h["u1"].complete


async def test_eip1271_contract_wallet(node):
    safe = "0x" + "5a" * 20
    node.code[safe] = "0x6080"
    node.valid_1271.add(safe)
    async with aiohttp.ClientSession() as session:
        reader = HoldingsReader(chain_settings(node.url), session)
        assert await reader.contract_signature_ok(safe, "hello", b"\x01" * 65)
        assert not await reader.contract_signature_ok(ALICE, "hello", b"\x01" * 65)  # no code: not a contract
