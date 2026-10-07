"""Pure logic: state tokens, signatures, nonces, tickets, roles, the draw, durations, embeds, CSV, the store."""
from __future__ import annotations

import csv
import io
import random
from collections import Counter

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from nacl.signing import SigningKey

from conftest import b58encode, make_settings
from fakes import make_world
from kit import statetoken
from kit.holdings import Holdings, role_changes, ticket_count, tickets_rule
from kit.nonces import NonceBook
from kit.raffles import (Entry, Raffle, card_embed, export_csv, mention_chunks, parse_duration, redraw_due,
                         refusal, wallet_columns, weighted_pick, winners_embed)
from kit.signatures import (decode_signature, eip191_hash, evm_message, evm_recover, is_magic,
                            is_valid_signature_call, normalize_address, solana_verify)

SECRET = b"k" * 32


# --- state tokens --------------------------------------------------------------------------------------
def test_state_token_good_tampered_expired_and_wrong_purpose():
    token = statetoken.sign(statetoken.VERIFY, {"u": "42", "n": "name"}, SECRET, 1800, now=1000)
    assert statetoken.verify(statetoken.VERIFY, token, SECRET, now=1500)["u"] == "42"
    body, mac = token.split(".")
    for bad in (body[:-2] + "xx." + mac, body + "." + "0" * 32, token + "x", "", "no-dot"):
        with pytest.raises(statetoken.TokenError, match="bad"):
            statetoken.verify(statetoken.VERIFY, bad, SECRET, now=1500)
    with pytest.raises(statetoken.TokenError, match="bad"):
        statetoken.verify(statetoken.VERIFY, token, b"other" * 8, now=1500)
    with pytest.raises(statetoken.TokenError, match="expired"):
        statetoken.verify(statetoken.VERIFY, token, SECRET, now=1000 + 1801)
    with pytest.raises(statetoken.TokenError, match="bad"):  # a verify link is not a login session
        statetoken.verify(statetoken.SESSION, token, SECRET, now=1500)


# --- signatures ----------------------------------------------------------------------------------------
def test_evm_sign_in_message_and_recovery():
    account = Account.create()
    message = evm_message("verify.example.com", "https://verify.example.com/verify", account.address.lower(),
                          "Link this wallet.", 1, "abc123", 0, 600)
    assert message.splitlines()[:2] == ["verify.example.com wants you to sign in with your Ethereum account:",
                                        account.address]  # EIP-55 checksummed
    assert "Issued At: 1970-01-01T00:00:00Z" in message and "Expiration Time: 1970-01-01T00:10:00Z" in message
    signature = "0x" + bytes(account.sign_message(encode_defunct(text=message)).signature).hex()
    assert evm_recover(message, signature) == account.address.lower()
    assert evm_recover(message, signature) != Account.create().address.lower()  # a different wallet does not match
    assert evm_recover(message + " ", signature) != account.address.lower()  # an edited message does not either
    assert evm_recover(message, "0x1234") is None


def test_solana_signature():
    key = SigningKey.generate()
    address = b58encode(bytes(key.verify_key))
    signature = key.sign(b"hello").signature
    assert solana_verify(address, "hello", signature)
    assert not solana_verify(address, "hello!", signature)
    assert not solana_verify(b58encode(bytes(SigningKey.generate().verify_key)), "hello", signature)
    assert not solana_verify("not-base58-0OIl", "hello", signature)


def test_eip1271_call_layout():
    data = is_valid_signature_call(eip191_hash("hi"), b"\x01" * 65)
    assert data.startswith("0x1626ba7e" + eip191_hash("hi").hex())
    words = [data[10 + i * 64:10 + (i + 1) * 64] for i in range((len(data) - 10) // 64)]
    assert int(words[1], 16) == 64 and int(words[2], 16) == 65 and len(words) == 6
    assert is_magic("0x1626ba7e" + "0" * 56) and not is_magic("0x" + "0" * 64) and not is_magic(None)


def test_addresses_and_signature_encodings():
    assert normalize_address("evm", " 0x" + "AB" * 20) == "0x" + "ab" * 20
    assert normalize_address("evm", "0x123") is None
    sol = b58encode(bytes(SigningKey.generate().verify_key))
    assert normalize_address("solana", sol) == sol and normalize_address("solana", "0" * 40) is None
    assert decode_signature("evm", "0x" + "11" * 65) == b"\x11" * 65
    assert decode_signature("solana", "AQ==") is None  # too short for any signature
    assert decode_signature("evm", "zz") is None


# --- nonces --------------------------------------------------------------------------------------------
def test_nonce_is_single_use_and_expires():
    now = [1000.0]
    book = NonceBook(ttl=600, clock=lambda: now[0])
    first = book.issue("1", "a", "evm", "0xabc", lambda n, i, e: f"sign {n} {i} {e}")
    assert first.message == f"sign {first.nonce} 1000.0 1600.0"
    assert book.take(first.nonce) == first
    assert book.take(first.nonce) is None  # used
    second = book.issue("1", "a", "evm", "0xabc", lambda *a: "m")
    now[0] += 601
    assert book.take(second.nonce) is None  # expired
    assert book.take("") is None


# --- tickets and roles ---------------------------------------------------------------------------------
def holdings(total=0, specials=None, complete=True, known=("Legendary",)):
    return Holdings({"Genesis": total}, specials or {}, complete, frozenset(known), 1)


def test_ticket_math():
    s = make_settings()
    assert ticket_count(None, s) == (0, [])
    assert ticket_count(holdings(0), s) == (0, [])
    assert ticket_count(holdings(1), s) == (1, ["1 NFT"])
    assert ticket_count(holdings(25), s) == (10, ["25 NFTs (max 10)"])
    assert ticket_count(holdings(2, {"Legendary": 1}), s) == (5, ["2 NFTs", "Legendary +3"])
    two = make_settings(raffles={"tickets_per_token": 2, "special_bonus": "sum"}, special=[
        {"name": "Rare", "role": "Rare", "collection": "Genesis", "token_ids": [1], "bonus_tickets": 1},
        {"name": "Epic", "role": "Epic", "collection": "Genesis", "token_ids": [2], "bonus_tickets": 2}])
    assert ticket_count(holdings(3, {"Rare": 1, "Epic": 1}), two) == (9, ["3 NFTs", "Rare +1", "Epic +2"])
    assert tickets_rule(two) == ("2 tickets per Test Project NFT in your linked wallets, up to 20\n"
                                 "Special bonus (each one held): Rare +1 / Epic +2")


def test_role_changes_only_touch_managed_roles_and_leave_unknown_parts_alone():
    s = make_settings()
    have = {"Holder", "Whale", "Unrelated", "Legendary"}
    assert role_changes(holdings(3), s, have) == ({"Collector"}, {"Whale", "Legendary"})
    assert role_changes(holdings(3, known=()), s, have) == ({"Collector"}, {"Whale"})  # specials unknown: kept
    assert role_changes(holdings(0, {"Legendary": 1}, complete=False), s, have) == (set(), set())
    assert role_changes(holdings(0), s, {"holder"}) == (set(), {"Holder"})  # names match ignoring case


def test_merged_reading_keeps_old_values_for_unknown_parts():
    old = holdings(4, {"Legendary": 1})
    fresh = Holdings({}, {}, False, frozenset(), 2)
    merged = fresh.merged_with(old)
    assert merged.total == 4 and merged.specials == {"Legendary": 1} and merged.complete
    assert Holdings.from_dict(merged.to_dict()) == merged


# --- raffle rules --------------------------------------------------------------------------------------
def test_parse_duration():
    assert parse_duration("24h") == 86400
    assert parse_duration("2d 12h") == 216000
    assert parse_duration("90m") == 5400
    assert parse_duration("1w") == 604800
    assert parse_duration("1W 1D") == 691200
    assert parse_duration("soon") == 0 and parse_duration("") == 0


def test_weighted_pick_no_repeats_and_weights():
    pool = {"a": 1, "b": 1, "c": 8, "zero": 0}
    picked = weighted_pick(pool, 10, random.Random(1))
    assert sorted(picked) == ["a", "b", "c"]  # no repeats, zero tickets never wins
    wins = Counter(weighted_pick(pool, 1, random.Random(seed))[0] for seed in range(4000))
    assert 0.76 < wins["c"] / 4000 < 0.84 and 0.07 < wins["a"] / 4000 < 0.13


async def test_gtd_and_fcfs_split(tmp_path, reader):
    w = make_world(tmp_path, make_settings(), reader, rng=random.Random(3))
    r = w.kit.create_raffle(title="T", link="https://x.example", chain="Ethereum", duration=600, gtd=2, fcfs=3,
                            created_by="1", channel_id="5", eligible=[])
    for k in range(10):
        uid, address = str(100 + k), "0x" + f"{k:040x}"
        w.kit.link_wallet(uid, f"u{k}", "evm", address)
        reader.counts[address] = 1
        w.kit.store.put_entry(Entry(r.id, uid, f"u{k}", {"address": address}, 1, k))
    gtd, fcfs = await w.kit.draw(r)
    assert len(gtd) == 2 and len(fcfs) == 3 and not set(gtd) & set(fcfs)
    w.kit.s = make_settings(raffles={"require_holding": False})
    reader.counts.clear()  # nobody holds anything any more: everyone still has 1 ticket
    gtd, fcfs = await w.kit.draw(r)
    assert len(gtd + fcfs) == 5


def make_raffle(**kw):
    base = dict(id="abc123", title="Drop", link="https://example.com", chain="Ethereum", wallet_kind="evm", gtd=2,
                fcfs=1, ends=2000, created=1000, created_by="1", channel_id="5")
    return Raffle(**{**base, **kw})


def test_refusals_before_any_chain_read():
    r = make_raffle(eligible=["9"])
    assert refusal(None, 1500, False, set()) == "closed"
    assert refusal(make_raffle(status="ended"), 1500, False, set()) == "closed"
    assert refusal(r, 2000, False, {"9"}) == "closing"
    assert refusal(r, 1500, True, {"9"}) == "closing"
    assert refusal(r, 1500, False, None) == "not_member"
    assert refusal(r, 1500, False, {"8"}) == "not_eligible"
    assert refusal(r, 1500, False, {"8", "9"}) is None
    assert refusal(make_raffle(), 1500, False, set()) is None  # no eligible roles: anyone in the server


def test_mention_chunks_fit_discord():
    ids = [str(10 ** 17 + k) for k in range(300)]
    chunks = mention_chunks(ids)
    assert all(len(c) <= 1900 for c in chunks) and " ".join(chunks).split() == [f"<@{u}>" for u in ids]
    assert mention_chunks([]) == []


def test_card_redraw_intervals():
    assert redraw_due(now=10_000, last_redraw=10_000 - 180, card_created=9_000)  # young card: every 3 minutes
    assert not redraw_due(now=10_000, last_redraw=10_000 - 179, card_created=9_000)
    assert not redraw_due(now=10_000, last_redraw=10_000 - 600, card_created=1_000)  # older than an hour: 15 min
    assert redraw_due(now=10_000, last_redraw=10_000 - 900, card_created=1_000)


def test_card_and_winners_embeds():
    card = card_embed(make_raffle(status="cancelled", image="https://example.com/i.png"), 4, [], "P Raffles", 1, "rule")
    assert card["title"] == "[ENDED] Drop" and card["color"] == 0x555555 and card["image"]["url"].endswith("i.png")
    assert {f["name"]: f["value"] for f in card["fields"]}["Eligible Roles"] == "—"
    assert card["footer"]["text"] == "P Raffles · abc123"
    small = winners_embed(make_raffle(), ["1"], ["2"], "P Raffles")
    assert small["description"].startswith("**GTD:** <@1>\n**FCFS:** <@2>\n\nCongratulations!")
    huge = winners_embed(make_raffle(), [str(10 ** 17 + k) for k in range(200)], ["5"], "P Raffles", "REROLL · ")
    assert huge["description"].startswith("**GTD:** 200 / **FCFS:** 1 winners — tagged above.")
    assert huge["title"] == "WINNERS: REROLL · Drop"


def test_csv_export_columns():
    entries = [Entry("abc123", "1", "ann", {"address": "0x1"}, 3, 1), Entry("abc123", "2", "bob", {"address": "0x2"}, 1, 2)]
    open_raffle = make_raffle()
    rows = list(csv.reader(io.StringIO(export_csv(open_raffle, entries, wallet_columns("evm"), everyone=False))))
    assert rows == [["discord_id", "discord_name", "wallet", "tickets"], ["1", "ann", "0x1", "3"], ["2", "bob", "0x2", "1"]]
    drawn = make_raffle(result={"gtd": ["2"], "fcfs": ["1"], "wallets": {"2": {"address": "0xprize"}}})
    rows = list(csv.reader(io.StringIO(export_csv(drawn, entries, wallet_columns("evm"), everyone=False))))
    assert rows == [["type", "discord_id", "discord_name", "wallet"], ["GTD", "2", "bob", "0xprize"],
                    ["FCFS", "1", "ann", "0x1"]]
    custom = wallet_columns("ordinals", ["taproot", "payment"])
    assert custom == [("wallet_taproot", "taproot"), ("wallet_payment", "payment")]
