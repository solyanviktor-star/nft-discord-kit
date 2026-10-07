"""The Discord layer driven with stand-ins: buttons, commands, the draw, tickets, roles."""
from __future__ import annotations

import csv
import io
import json
import random
import time
from types import SimpleNamespace

import discord
import pytest

from conftest import b58encode, make_settings
from fakes import FakeMessage, FakeUser, forbidden, make_world, next_id
from kit.bot import raffles as rb
from kit.bot import support as sb
from kit.bot import verify as vb
from kit.bot.commands import cmd_check, cmd_me
from kit.bot.common import can_run_raffles, is_admin
from kit.raffles import ENDED, OPEN

EVM_A = "0x" + "aa" * 20
EVM_B = "0x" + "bb" * 20
SOL = b58encode(bytes([5] * 32))  # a made-up Solana address
TAPROOT = "bc1p" + "q" * 58
PAYMENT = "bc1q" + "w" * 38


@pytest.fixture
async def world(tmp_path, settings, reader):
    w = make_world(tmp_path, settings, reader, rng=random.Random(7))
    yield w
    await w.bot.settle()  # let background card redraws finish


def new_raffle(w, chain="Ethereum", gtd=1, fcfs=0, ends_in=3600, eligible=None):
    r = w.kit.create_raffle(title="Test Drop", link="https://example.com/drop", chain=chain, duration=3600, gtd=gtd,
                            fcfs=fcfs, created_by=str(w.staff.id), channel_id=str(w.channels["giveaways"].id),
                            eligible=[str(w.role("Holder").id)] if eligible is None else eligible)
    r.ends = int(time.time()) + ends_in
    card = FakeMessage(next_id(), w.channels["giveaways"])
    w.channels["giveaways"].messages.append(card)
    r.message_id = str(card.id)
    w.kit.store.save_raffle(r)
    return r


def link(w, member, address=EVM_A, kind="evm", count=3, specials=()):
    w.kit.link_wallet(str(member.id), member.name, kind, address)
    w.reader.counts[address] = count
    w.reader.specials[address] = set(specials)


# --- Enter ---------------------------------------------------------------------------------------------
async def test_enter_happy_path_then_already_in(world):
    r = new_raffle(world)
    link(world, world.holder)
    i = world.interaction(world.holder)
    await rb.enter_raffle(world.bot, i, r.id)
    assert i.log[0][0] == "defer"
    assert i.replies == [f"You're in! **3 tickets** (3 NFTs) · wallet `{EVM_A}`"]
    entry = world.kit.store.entry(r.id, str(world.holder.id))
    assert entry.tickets == 3 and entry.wallet == {"address": EVM_A}
    again = world.interaction(world.holder)
    await rb.enter_raffle(world.bot, again, r.id)
    assert again.replies == [f"You're already in ✅ · **3 tickets** (3 NFTs) · wallet `{EVM_A}`"]


async def test_enter_tickets_cap_and_special_bonus(world):
    r = new_raffle(world)
    link(world, world.holder, count=14, specials=["Legendary"])
    i = world.interaction(world.holder)
    await rb.enter_raffle(world.bot, i, r.id)
    assert i.replies == [f"You're in! **13 tickets** (14 NFTs (max 10), Legendary +3) · wallet `{EVM_A}`"]


@pytest.mark.parametrize("case, expected", [
    ("closed", "This raffle has ended."),
    ("closing", "This raffle is closing right now."),
    ("drawing", "This raffle is closing right now."),
    ("not_eligible", "Only holders can enter. Verify in <#{verify}> to get your role — it is automatic after that."),
    ("no_profile", "Link your wallet once and you are in for every raffle after that."),
    ("rpc_busy", "Chain RPC is busy - try again in a minute."),
    ("no_pass", "No Test Project NFT found in your linked wallets — if yours sits in another wallet, link THAT "
                "wallet too and press Verify."),
    ("no_evm_wallet", "No EVM wallet linked — link one first."),
    ("no_solana_wallet", "This raffle is on Solana — add your Solana wallet first."),
    ("no_custom_wallet", "This raffle is on Bitcoin — add your Bitcoin wallet first."),
])
async def test_enter_refusals(world, case, expected):
    chain = {"no_solana_wallet": "Solana", "no_custom_wallet": "Bitcoin"}.get(case, "Ethereum")
    r = new_raffle(world, chain=chain)
    member = world.holder
    if case == "closed":
        world.kit.cancel_raffle(r)
    elif case == "closing":
        r.ends = int(time.time()) - 5
        world.kit.store.save_raffle(r)
    elif case == "drawing":
        world.kit.drawing.add(r.id)
    elif case == "not_eligible":
        member = world.newbie
    elif case == "rpc_busy":
        link(world, member)
        world.reader.down = True
    elif case == "no_pass":
        link(world, member, count=0)
    elif case == "no_evm_wallet":
        link(world, member, address=SOL, kind="solana")
    elif case in ("no_solana_wallet", "no_custom_wallet"):
        link(world, member)
    i = world.interaction(member)
    await rb.enter_raffle(world.bot, i, r.id)
    assert i.replies == [expected.format(verify=world.channels["verify"].id)]
    assert world.kit.store.entry(r.id, str(member.id)) is None
    if case in ("no_profile", "no_evm_wallet", "no_solana_wallet", "no_custom_wallet"):
        view = i.last[2]["view"]  # a button that fixes it: the personal verify link
        assert view.children[0].url.startswith("https://verify.example.com/verify?state=")


async def test_enter_solana_and_custom_kind_wallets(world):
    sol = new_raffle(world, chain="Solana")
    btc = new_raffle(world, chain="Bitcoin")
    link(world, world.holder)
    uid = str(world.holder.id)
    world.kit.set_raffle_wallet(uid, "solana", {"address": SOL})
    world.kit.set_raffle_wallet(uid, "ordinals", {"taproot": TAPROOT, "payment": PAYMENT})
    for r, wallet in ((sol, {"address": SOL}), (btc, {"taproot": TAPROOT, "payment": PAYMENT})):
        i = world.interaction(world.holder)
        await rb.enter_raffle(world.bot, i, r.id)
        assert i.replies[0].startswith("You're in!")
        assert world.kit.store.entry(r.id, uid).wallet == wallet


async def test_entries_redraw_the_card_once_then_wait(world):
    r = new_raffle(world, eligible=[])
    link(world, world.holder)
    link(world, world.staff, address=EVM_B)
    card = world.channels["giveaways"].messages[-1]
    await rb.enter_raffle(world.bot, world.interaction(world.holder), r.id)
    await world.bot.settle()
    await rb.enter_raffle(world.bot, world.interaction(world.staff), r.id)
    await world.bot.settle()
    assert len(card.edits) == 1  # the second entry waits for the interval
    assert r.id in world.bot.cards.pending
    entrants = next(f for f in card.edits[0]["embed"].fields if f.name == "Entrants")
    assert entrants.value == "1"


# --- create ------------------------------------------------------------------------------------------
async def test_create_posts_the_card(world):
    world.role("Giveaway Alerts").mentionable = False
    i = world.interaction(world.staff, world.channels["giveaways"])
    await rb.cmd_create(world.bot, i, "Cool Project", "https://example.com/cool", "Ethereum", "2d 12h", 3, 2,
                        "Mint soon", "https://example.com/cool.png")
    [msg] = world.channels["giveaways"].messages
    embed = msg.embeds[0]
    assert msg.content == world.role("Giveaway Alerts").mention  # the alert tag (the bot may mention roles here)
    assert embed.title == "Cool Project"
    assert embed.description == "https://example.com/cool\n\nMint soon"
    fields = {f.name: f.value for f in embed.fields}
    assert list(fields) == ["Ends", "Chain", "Winners", "Entrants", "Guaranteed", "FCFS", "Eligible Roles", "Tickets"]
    assert fields["Chain"] == "Ethereum" and fields["Winners"] == "5" and fields["Entrants"] == "0"
    assert fields["Guaranteed"] == "3" and fields["FCFS"] == "2"
    for name in ("Whale", "Collector", "Holder", "Legendary"):  # default: every holder role
        assert world.role(name).mention in fields["Eligible Roles"]
    assert fields["Tickets"].startswith("1 ticket per Test Project NFT in your linked wallets, up to 10")
    assert "Special bonus (the best one): " + world.role("Legendary").mention + " +3" in fields["Tickets"]
    assert embed.image.url == "https://example.com/cool.png"
    assert [c.custom_id for c in msg.view.children][0].startswith("rf:enter:")
    r = world.kit.store.raffles()[0]
    assert r.message_id == str(msg.id) and r.ends - r.created == 2 * 86400 + 12 * 3600
    assert i.replies == [f"Raffle `{r.id}` posted in {world.channels['giveaways'].mention} — ends <t:{r.ends}:f>."]


async def test_create_checks(world):
    cases = [(world.newbie, "24h", 1, "https://x.example", "Team only."),
             (world.staff, "soon", 1, "https://x.example", "Duration looks wrong — try `24h`, `2d`, `90m`."),
             (world.staff, "24h", 0, "https://x.example", "Need at least one winner (gtd + fcfs)."),
             (world.staff, "24h", 1, "x.example", "Link must start with https://")]
    for user, duration, gtd, link_, reply in cases:
        i = world.interaction(user)
        await rb.cmd_create(world.bot, i, "T", link_, "Ethereum", duration, gtd)
        assert i.replies == [reply]
    assert world.kit.store.raffles() == []


async def test_raffle_users_and_managers_may_run_raffles(world):
    helper = world.guild.add_member(4242, "helper")  # listed in discord.raffle_users
    manager = world.guild.add_member(5005, "manager", "Raffle Manager")
    for user in (helper, manager):
        i = world.interaction(user)
        await rb.cmd_create(world.bot, i, "T", "https://x.example", "Ethereum", "1h", 1)
        assert i.replies[-1].startswith("Raffle `")
    i = world.interaction(helper)
    await cmd_check(world.bot, i, world.holder)  # but not the other team commands
    assert i.replies == ["Team only."]


async def test_create_without_permission_to_tag_skips_the_tag(world):
    world.channels["giveaways"].mention_everyone = False
    await rb.cmd_create(world.bot, world.interaction(world.staff), "T", "https://x.example", "Ethereum", "1h", 1)
    assert world.channels["giveaways"].messages[0].content is None


async def test_create_failure_removes_the_raffle(world):
    async def broken(*a, **kw):
        raise forbidden()
    world.channels["giveaways"].send = broken
    i = world.interaction(world.staff)
    await rb.cmd_create(world.bot, i, "T", "https://x.example", "Ethereum", "1h", 1)
    assert world.kit.store.raffles() == []
    assert "Could not post in" in i.replies[0]


# --- the draw ----------------------------------------------------------------------------------------
def crowd(world, n):
    members = []
    for k in range(n):
        m = world.guild.add_member(10 ** 17 + k, f"fan{k}", "Holder")  # Discord-sized ids, made up
        address = "0x" + f"{k:040x}"
        world.kit.link_wallet(str(m.id), m.name, "evm", address)
        world.reader.counts[address] = 1
        members.append(m)
    return members


async def enter_all(world, r, members):
    for m in members:
        await rb.enter_raffle(world.bot, world.interaction(m), r.id)


async def test_finish_announces_in_chunks_and_ends_the_raffle(world):
    r = new_raffle(world, gtd=200)
    fans = crowd(world, 220)
    await enter_all(world, r, fans)
    gtd, fcfs = await rb.finish(world.bot, r)
    assert len(gtd) == 200 and len(set(gtd)) == 200 and fcfs == []
    sent = world.channels["winners"].messages
    assert len(sent) > 1 and all(len(m.content) < 2000 for m in sent)
    mentioned = " ".join(m.content for m in sent)
    assert all(f"<@{u}>" in mentioned for u in gtd)
    head = sent[0]
    assert head.embeds[0].title == "WINNERS: Test Drop"
    assert head.embeds[0].description.startswith("**GTD:** 200 winners — tagged above.")  # counts: too long
    assert head.view.children[0].label == "View Giveaway"
    done = world.kit.store.raffle(r.id)
    assert done.status == ENDED and done.result["gtd"] == gtd and done.result["winners_msg"] == str(head.id)
    card = world.channels["giveaways"].messages[0]
    assert card.edits[-1]["embed"].title == "[ENDED] Test Drop"
    assert card.edits[-1]["view"].children[0].item.disabled
    assert r.id not in world.kit.drawing


async def test_draw_recounts_and_drops_sellers(world):
    r = new_raffle(world, gtd=3)
    fans = crowd(world, 3)
    await enter_all(world, r, fans)
    world.reader.counts["0x" + f"{1:040x}"] = 0  # fan1 sold before the deadline
    gtd, _ = await rb.finish(world.bot, r)
    assert sorted(gtd) == sorted([str(fans[0].id), str(fans[2].id)])


async def test_no_entrants(world):
    r = new_raffle(world)
    await rb.finish(world.bot, r)
    [msg] = world.channels["winners"].messages
    assert msg.content == "No eligible entrants."
    assert world.kit.store.raffle(r.id).status == ENDED


async def test_reroll_excludes_previous_winners(world):
    r = new_raffle(world, gtd=2)
    fans = crowd(world, 5)
    await enter_all(world, r, fans)
    first, _ = await rb.finish(world.bot, r)
    i = world.interaction(world.staff)
    await rb.cmd_reroll(world.bot, i, r.id, 2)
    result = world.kit.store.raffle(r.id).result
    new = result["gtd"][2:]
    assert len(new) == 2 and not set(new) & set(first)
    assert result["rerolls"][0]["users"] == new
    assert world.channels["winners"].messages[-1].embeds[0].title == "WINNERS: REROLL · Test Drop"
    assert i.replies == [f"Rerolled `{r.id}`: 2 new winner(s)."]
    last = world.interaction(world.staff)
    await rb.cmd_reroll(world.bot, last, f"`{r.id.upper()}`", 5)  # ids are cleaned; only one entrant is left
    assert last.replies == [f"Rerolled `{r.id}`: 1 new winner(s)."]


async def test_end_list_cancel(world):
    r = new_raffle(world)
    other = new_raffle(world)
    i = world.interaction(world.staff)
    await rb.cmd_list(world.bot, i)
    assert f"`{r.id}` Test Drop · Ethereum · OPEN · 0 entrants" in i.replies[0]
    i = world.interaction(world.staff)
    await rb.cmd_cancel(world.bot, i, other.id)
    assert i.replies == [f"Cancelled `{other.id}`."]
    assert world.kit.store.raffle(other.id).status == "cancelled"
    i = world.interaction(world.staff)
    await rb.cmd_end(world.bot, i, other.id)
    assert i.replies == ["No open raffle with that id."]
    i = world.interaction(world.staff)
    await rb.cmd_end(world.bot, i, r.id)
    assert i.replies == [f"Ended `{r.id}`: 0 GTD, 0 FCFS winners posted."]


async def test_export_csv(world):
    r = new_raffle(world, gtd=1, fcfs=1)
    fans = crowd(world, 3)
    await enter_all(world, r, fans)
    i = world.interaction(world.staff)
    await rb.cmd_export(world.bot, i, r.id)  # not drawn yet: every entrant
    rows = list(csv.reader(io.StringIO(i.last[2]["file"].fp.read().decode())))
    assert rows[0] == ["discord_id", "discord_name", "wallet", "tickets"] and len(rows) == 4
    await rb.finish(world.bot, r)
    i = world.interaction(world.staff)
    await rb.cmd_export(world.bot, i, r.id)
    rows = list(csv.reader(io.StringIO(i.last[2]["file"].fp.read().decode())))
    assert rows[0] == ["type", "discord_id", "discord_name", "wallet"]
    assert [row[0] for row in rows[1:]] == ["GTD", "FCFS"]
    assert i.last[2]["file"].filename == f"test-drop-x-test-project-winners-{r.id}.csv"
    btc = new_raffle(world, chain="Bitcoin")
    world.kit.set_raffle_wallet(str(fans[0].id), "ordinals", {"taproot": TAPROOT, "payment": PAYMENT})
    await rb.enter_raffle(world.bot, world.interaction(fans[0]), btc.id)
    i = world.interaction(world.staff)
    await rb.cmd_export(world.bot, i, btc.id, everyone=True)
    rows = list(csv.reader(io.StringIO(i.last[2]["file"].fp.read().decode())))
    assert rows[0] == ["discord_id", "discord_name", "wallet_taproot", "wallet_payment", "tickets"]
    assert rows[1][2:4] == [TAPROOT, PAYMENT]


async def test_ticker_draws_due_raffles_even_when_one_fails(world):
    broken = new_raffle(world, ends_in=-10)
    fine = new_raffle(world, ends_in=-10)
    real_send = world.channels["winners"].send
    calls = {"n": 0}

    async def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise forbidden()
        return await real_send(*a, **kw)

    world.channels["winners"].send = flaky
    await world.bot.tick(1, set())
    statuses = {r: world.kit.store.raffle(r).status for r in (broken.id, fine.id)}
    assert sorted(statuses.values()) == [ENDED, OPEN]  # the failing one stays open, the other is drawn
    failed = next(r for r, status in statuses.items() if status == OPEN)
    assert world.bot._retry[failed][1] == 60  # retried after a minute, not on every 30-second tick
    await world.bot.tick(1, set())
    assert calls["n"] == 2 and world.kit.store.raffle(failed).status == OPEN
    world.bot._retry[failed] = (0.0, 60)  # the minute has passed
    await world.bot.tick(1, set())
    assert world.kit.store.raffle(failed).status == ENDED and failed not in world.bot._retry


async def test_no_winners_channel_backs_off_and_says_why(world, caplog, tmp_path):
    r = new_raffle(world, ends_in=-10)
    world.bot.s = world.kit.s = make_settings(channels={"winners": "renamed-away"})
    world.bot.uncached.add(int(r.channel_id))
    lost = world.channels["giveaways"]
    del world.guild.channels[lost.id]  # neither cached nor fetchable
    await world.bot.tick(1, set())
    assert world.kit.store.raffle(r.id).status == OPEN and world.bot._retry[r.id][1] == 60
    assert "no channel to announce the winners in" in caplog.text and "next try in 1 minutes" in caplog.text
    world.bot._retry[r.id] = (0.0, 60)
    await world.bot.tick(1, set())
    assert world.bot._retry[r.id][1] == 120  # doubling, up to an hour
    world.guild.channels[lost.id] = lost  # back, but only through fetch_channel
    world.bot._retry[r.id] = (0.0, 120)
    await world.bot.tick(1, set())
    assert world.kit.store.raffle(r.id).status == ENDED and lost.messages[-1].content == "No eligible entrants."


# --- cards -------------------------------------------------------------------------------------------
async def test_card_refresh_throttle_and_deleted_card(world):
    r = new_raffle(world)
    card = world.channels["giveaways"].messages[-1]
    cards = world.bot.cards
    await cards.refresh(r)
    await cards.refresh(r)
    assert len(card.edits) == 1 and r.id in cards.pending
    await cards.refresh(r, force=True)
    assert len(card.edits) == 2 and r.id not in cards.pending
    world.channels["giveaways"].deleted.add(card.id)
    await cards.refresh(r, force=True)
    assert world.kit.store.raffle(r.id).card_gone == r.message_id
    await cards.refresh(world.kit.store.raffle(r.id), force=True)  # no more attempts for a deleted card
    assert len(card.edits) == 2


# --- verify, wallets, roles ----------------------------------------------------------------------------
async def test_verify_first_time_then_on_chain_check(world):
    i = world.interaction(world.newbie)
    await vb.run_verify(world.bot, i, "test")
    assert i.replies[0].startswith("**Step 1.** Press **Link Wallet** below, sign once on verify.example.com")
    assert i.last[2]["view"].children[0].url.startswith("https://verify.example.com/verify?state=")
    link(world, world.newbie, count=3, specials=["Legendary"])
    i = world.interaction(world.newbie)
    await vb.run_verify(world.bot, i, "test")
    assert i.replies == ["On-chain right now: **3 NFTs** + Legendary across 1 linked wallet. Roles updated. "
                         "Raffle tickets: **6** (3 NFTs, Legendary +3)"]
    assert {r.name for r in world.newbie.roles} == {"Holder", "Collector", "Legendary"}
    world.reader.down = True
    i = world.interaction(world.newbie)
    await vb.run_verify(world.bot, i, "test")
    assert i.replies == ["Chain RPC is busy - try again in a minute."]


async def test_role_sync_moves_only_kit_roles(world):
    member = world.guild.add_member(7007, "mixed", "Holder", "Whale", "Unrelated", "Team")
    link(world, member, count=3)
    report = await world.kit.sync_user(str(member.id), "test")
    assert report.added == ["Collector"] and report.removed == ["Whale"]
    assert {r.name for r in member.roles} == {"Holder", "Collector", "Unrelated", "Team"}
    world.reader.down = True  # a silent node is not "sold everything": nothing moves
    report = await world.kit.sync_user(str(member.id), "test")
    assert report.holdings is None and report.added == report.removed == []
    assert {r.name for r in member.roles} == {"Holder", "Collector", "Unrelated", "Team"}


async def test_role_sync_reports_hierarchy_problems(world):
    link(world, world.newbie)
    world.newbie.forbid_roles = True
    i = world.interaction(world.newbie)
    await vb.run_verify(world.bot, i, "test")
    assert "Roles could not be updated: the bot may not manage these roles" in i.replies[0]


async def test_ticker_gives_fresh_links_their_roles(world):
    known = set(world.kit.store.owners())
    link(world, world.newbie, count=1)
    await world.bot.tick(1, known)
    assert [r.name for r in world.newbie.roles] == ["Holder"]


async def test_raffle_wallet_modals(world):
    r = new_raffle(world)
    uid = str(world.holder.id)
    i = world.interaction(world.holder)
    await vb.save_raffle_wallet(world.bot, i, "evm", {"address": EVM_B})
    assert i.replies == ["Link your wallet first (Link Wallet), then set the raffle wallet."]
    link(world, world.holder)
    await rb.enter_raffle(world.bot, world.interaction(world.holder), r.id)
    i = world.interaction(world.holder)
    await vb.save_raffle_wallet(world.bot, i, "evm", {"address": "0x123"})
    assert i.replies == ["That does not look like an EVM address — 0x and 40 hex characters."]
    i = world.interaction(world.holder)
    await vb.save_raffle_wallet(world.bot, i, "evm", {"address": EVM_B})
    assert i.replies == [f"Raffle wallet saved: `{EVM_B}` — prizes go there. Tickets still come from the NFTs "
                         "in your linked wallets. Updated on 1 open raffle."]
    assert world.kit.store.entry(r.id, uid).wallet == {"address": EVM_B}  # the entry follows the new wallet
    i = world.interaction(world.holder)
    await vb.save_raffle_wallet(world.bot, i, "evm", {"address": ""})
    assert i.replies[0].startswith("Raffle wallet reset — prizes go to your primary wallet again.")
    assert world.kit.store.entry(r.id, uid).wallet == {"address": EVM_A}
    i = world.interaction(world.holder)
    await vb.save_raffle_wallet(world.bot, i, "ordinals", {"taproot": TAPROOT, "payment": "nope"})
    assert i.replies == ["That does not look like a valid Payment address."]
    i = world.interaction(world.holder)
    await vb.save_raffle_wallet(world.bot, i, "solana", {"address": SOL})
    assert i.replies == [f"Solana wallet saved: `{SOL}`"]


async def test_my_wallets_and_unlink(world):
    i = world.interaction(world.holder)
    await vb.show_wallets(world.bot, i)
    assert i.last[2]["embed"].description.startswith("No wallet linked yet. Press **Link Wallet**")
    link(world, world.holder, count=2)
    await world.kit.sync_user(str(world.holder.id), "test")
    i = world.interaction(world.holder)
    await vb.show_wallets(world.bot, i)
    text = i.last[2]["embed"].description
    assert f"Raffle wallet (EVM): `{EVM_A}` (primary)" in text and "Test Project: holder ×2" in text
    assert "Raffle tickets: **2** (2 NFTs)" in text
    labels = [getattr(c, "label", None) for c in i.last[2]["view"].children]
    assert labels[:3] == ["Link Another Wallet", "Set Raffle Wallet", "Set Solana Wallet"]
    i = world.interaction(world.holder)
    await vb.unlink(world.bot, i, f"evm:{EVM_A}")
    assert i.replies[0].startswith("Unlinked `0xaaaa…aaaa`.")
    assert world.kit.store.wallets_of(str(world.holder.id)) == []
    assert "Holder" not in {r.name for r in world.holder.roles}  # no wallets left: kit roles go


async def test_me_and_check(world):
    link(world, world.holder, count=4)
    i = world.interaction(world.holder)
    await cmd_me(world.bot, i)
    fields = {f.name: f.value for f in i.last[2]["embed"].fields}
    assert fields["Raffle wallet (EVM)"] == f"`{EVM_A}` · primary" and fields["Won"] == "nothing yet"
    i = world.interaction(world.newbie)
    await cmd_check(world.bot, i, world.holder)
    assert i.replies == ["Team only."]
    i = world.interaction(world.staff)
    await cmd_check(world.bot, i, world.holder)
    fields = {f.name: f.value for f in i.last[2]["embed"].fields}
    assert fields["Signed wallets (1)"] == "`0xaaaa…aaaa` — 4" and fields["On-chain total"] == "**4** NFT(s)"
    i = world.interaction(world.staff)
    await cmd_check(world.bot, i, FakeUser(999, "stranger"))
    assert i.last[2]["embed"].description.startswith("**No wallet linked.**")


# --- tickets -------------------------------------------------------------------------------------------
async def test_ticket_open_and_close(world):
    world.guild.add_member(2002, "moderator", "Mod")
    link(world, world.holder, count=2)
    await world.kit.sync_user(str(world.holder.id), "test")
    i = world.interaction(world.holder, world.channels["tickets"])
    await sb.ticket_start(world.bot, i, "support")
    assert i.log[0][0] == "modal" and i.log[0][1].cat.name == "Support"
    i = world.interaction(world.holder, world.channels["tickets"])
    await i.response.defer()
    await sb.ticket_open(world.bot, i, i.client.s.support.categories[1], "My prize never arrived")
    [thread] = world.channels["tickets"].threads
    assert thread.created_with["name"] == "Support | Holder" and thread.created_with["invitable"] is False
    assert thread.created_with["auto_archive_duration"] == 4320
    first, greeting = thread.messages
    assert first.content.split() == [world.holder.mention, world.staff.mention, "<@2002>"]  # staff one by one
    fields = {f.name: f.value for f in first.embeds[0].fields}
    assert fields["Test Project on chain"].startswith("2 NFTs")
    assert fields["Roles"] == "Holder" and fields["Wallets"] == "`0xaaaa…aaaa`"
    assert greeting.content.startswith("**Test Project:** Thanks!")
    assert i.replies[-1] == f"Your ticket is open: {thread.mention} — the team is pinged there."
    assert "Ticket #1 opened" in world.channels["ticket_log"].messages[0].embeds[0].title
    again = world.interaction(world.holder, world.channels["tickets"])
    await sb.ticket_start(world.bot, again, "other")
    assert again.replies == [f"You already have an open ticket: {thread.mention} — write there, or close it first."]

    thread.messages.append(FakeMessage(1, thread, f"Hi {world.staff.mention}, see tx", author=world.holder))
    stranger = world.interaction(world.newbie, thread)
    await sb.ticket_close(world.bot, stranger)
    assert stranger.replies == ["Only the team or the person who opened it can close a ticket."]
    closing = world.interaction(world.holder, thread)
    await sb.ticket_close(world.bot, closing, "solved")
    assert closing.replies == ["Closed — the transcript went to the log."]
    log_msg = world.channels["ticket_log"].messages[-1]
    assert "Ticket #1 closed" in log_msg.embeds[0].title and "3 messages · solved" in log_msg.embeds[0].description
    text = log_msg.file.fp.read().decode()
    assert text.startswith("Ticket #1 · Support · opened by holder · closed by holder")
    assert "holder: Hi @staffer, see tx" in text
    assert thread.archived and thread.locked and thread.removed == [world.holder]
    assert world.kit.store.ticket(str(thread.id))["status"] == "closed"
    saved = json.loads((world.bot.data_dir / "tickets" / f"0001-{thread.id}.json").read_text(encoding="utf-8"))
    assert saved["ticket"]["note"] == "solved" and len(saved["messages"]) == 3
    twice = world.interaction(world.holder, thread)
    await sb.ticket_close(world.bot, twice)
    assert twice.replies == ["Already closed."]


async def test_ticket_mentions_a_mentionable_staff_role_and_handles_missing_rights(world):
    world.role("Team").mentionable = True
    i = world.interaction(world.holder, world.channels["tickets"])
    await i.response.defer()
    await sb.ticket_open(world.bot, i, i.client.s.support.categories[0], "Question")
    first = world.channels["tickets"].threads[0].messages[0]
    assert first.content == f"{world.holder.mention} {world.role('Team').mention}"
    world.channels["tickets"].fail_create_thread = forbidden()
    i = world.interaction(world.newbie, world.channels["tickets"])
    await i.response.defer()
    await sb.ticket_open(world.bot, i, i.client.s.support.categories[0], "Question")
    assert "the bot lacks Create Private Threads there" in i.replies[0]


async def test_empty_transcript_points_at_the_message_content_intent(world, caplog):
    i = world.interaction(world.holder, world.channels["tickets"])
    await i.response.defer()
    await sb.ticket_open(world.bot, i, i.client.s.support.categories[0], "Question")
    [thread] = world.channels["tickets"].threads
    thread.messages.append(FakeMessage(1, thread, "", author=world.holder))  # what Discord sends without the intent
    closing = world.interaction(world.holder, thread)
    await sb.ticket_close(world.bot, closing)
    assert "turn on MESSAGE CONTENT INTENT" in caplog.text
    text = world.channels["ticket_log"].messages[-1].file.fp.read().decode()
    assert "MESSAGE CONTENT INTENT" in text.splitlines()[1]


async def test_ticket_button_checks_the_cache_only(world):
    i = world.interaction(world.holder, world.channels["tickets"])
    await i.response.defer()
    await sb.ticket_open(world.bot, i, i.client.s.support.categories[0], "Question")
    [thread] = world.channels["tickets"].threads
    world.bot.uncached.add(thread.id)  # open, but not in the cache: the button must not fetch (3-second limit)
    press = world.interaction(world.holder, world.channels["tickets"])
    await sb.ticket_start(world.bot, press, "support")
    assert press.log[0][0] == "modal"
    submit = world.interaction(world.holder, world.channels["tickets"])
    await submit.response.defer()
    await sb.ticket_open(world.bot, submit, submit.client.s.support.categories[1], "Again")
    assert submit.replies == [f"You already have an open ticket: {thread.mention}"]  # the submit fetches


async def test_close_outside_a_ticket(world):
    i = world.interaction(world.staff, world.channels["giveaways"])
    await sb.ticket_close(world.bot, i)
    assert i.replies == ["This is not a ticket thread of mine."]


# --- self-roles ---------------------------------------------------------------------------------------
async def test_self_role_toggle(world):
    i = world.interaction(world.newbie)
    await sb.toggle_self_role(world.bot, i, "giveaway-alerts")
    assert i.log[0][0] == "defer"  # answered before the role change goes to Discord
    assert i.replies == [f"You now have {world.role('Giveaway Alerts').mention}."]
    i = world.interaction(world.newbie)
    await sb.toggle_self_role(world.bot, i, "giveaway-alerts")
    assert i.replies == [f"Removed {world.role('Giveaway Alerts').mention}."]
    i = world.interaction(world.newbie)
    await sb.toggle_self_role(world.bot, i, "nope")
    assert i.replies == ["This role is not available right now."]


async def test_self_role_errors_are_answered(world):
    async def broken(*roles, reason=""):
        raise discord.HTTPException(SimpleNamespace(status=500, reason="Server Error"), "boom")
    world.newbie.add_roles = broken
    i = world.interaction(world.newbie)
    await sb.toggle_self_role(world.bot, i, "giveaway-alerts")
    assert i.replies == ["Discord did not take that right now (500). Try again in a minute."]
    world.newbie.forbid_roles = True
    del world.newbie.add_roles
    i = world.interaction(world.newbie)
    await sb.toggle_self_role(world.bot, i, "giveaway-alerts")
    assert i.replies[0].startswith("I cannot manage that role")


async def test_panel_command_defers_before_posting(world):
    i = world.interaction(world.staff, world.channels["verify"])
    await rb.cmd_panel(world.bot, i)
    assert [kind for kind, _, _ in i.log] == ["defer", "followup"] and i.replies == ["Panel posted."]
    panel = world.channels["verify"].messages[-1]
    assert panel.embeds[0].title == "Verify your Test Project holdings"
    assert [c.custom_id for c in panel.view.children] == ["kit:verify", "kit:wallets"]


async def test_setup_hook_survives_a_failed_command_sync(world, caplog):
    async def refused(guild=None):
        raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), {"code": 50001, "message": "Missing Access"})

    async def no_ticker():
        return None

    world.bot.tree.sync = refused
    world.bot.ticker = no_ticker
    await world.bot.setup_hook()  # must not raise: a crash here would restart the process forever
    assert "could not register the /nft commands" in caplog.text and "invite-url" in caplog.text


async def test_staff_and_manager_roles_match_whole_names(world):
    for name in ("Model Citizen", "\U0001F6E1 Mod", "Raffle Managers Fan Club", "raffle manager"):
        world.guild.add_role(name)
    lookalike = world.guild.add_member(8001, "lookalike", "Model Citizen")
    shielded = world.guild.add_member(8002, "shielded", "\U0001F6E1 Mod")
    fan = world.guild.add_member(8003, "fan", "Raffle Managers Fan Club")
    lower = world.guild.add_member(8004, "lower", "raffle manager")
    assert not is_admin(world.bot, lookalike) and is_admin(world.bot, shielded)
    assert not can_run_raffles(world.bot, fan) and can_run_raffles(world.bot, lower)


async def test_renamed_kit_role_is_still_managed(world):
    holder = world.role("Holder")
    (world.bot.data_dir / "state.json").write_text(json.dumps({"roles": {"Holder": str(holder.id)}}), encoding="utf-8")
    holder.name = "\U0001F48E Holders"  # renamed in Discord after the build
    link(world, world.holder, count=1)
    report = await world.kit.sync_user(str(world.holder.id), "test")
    assert report.added == [] and world.holder.roles.count(holder) == 1  # already has it: nothing added
    world.reader.counts[EVM_A] = 0
    report = await world.kit.sync_user(str(world.holder.id), "test")
    assert report.removed == ["Holder"] and holder not in world.holder.roles


async def test_giveaways_overview(world):
    r = new_raffle(world)
    link(world, world.holder)
    await rb.enter_raffle(world.bot, world.interaction(world.holder), r.id)
    embed = rb.giveaways_embed(world.bot, str(world.holder.id))
    assert "**IN** (3 tickets)" in embed.description and embed.footer.text == "Your raffle tickets: 3 (3 NFTs)"

