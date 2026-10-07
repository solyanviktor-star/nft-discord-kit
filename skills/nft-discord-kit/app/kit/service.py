"""The kit's core, shared by the bot and the website: wallets, holdings, roles and raffle entries.

Discord is reached only through `DiscordPort` (the bot implements it, tests fake it) and the chain
only through `Reader`, so this module never imports discord.py and every rule here is testable.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Mapping, Protocol, Sequence

from . import statetoken
from .config import EVM_ADDRESS, SOLANA_ADDRESS, Settings
from .holdings import Holdings, role_changes, ticket_count, wanted_roles
from .raffles import (CANCELLED, ENDED, OPEN, Entry, Raffle, entry_message, new_raffle_id, refusal, wallet_text,
                      weighted_pick)
from .store import Store

log = logging.getLogger("kit")
VERIFY_TTL = 30 * 60  # the personal verify link works for 30 minutes


@dataclass(frozen=True)
class MemberInfo:
    id: str
    name: str
    role_ids: frozenset[str]
    role_names: frozenset[str]


class DiscordPort(Protocol):
    async def get_member(self, user_id: str) -> MemberInfo | None: ...

    async def edit_roles(self, user_id: str, add: set[str], remove: set[str], reason: str) -> list[str]:
        """Add/remove roles by name; returns problems (missing role, no permission), empty when all went well."""

    async def add_member(self, user_id: str, access_token: str, roles: set[str]) -> bool: ...

    def raffle_changed(self, raffle_id: str) -> None:
        """The raffle's card should be redrawn (the bot throttles this)."""


class Reader(Protocol):
    async def read(self, owners: Mapping[str, Sequence[tuple[str, str]]]) -> dict[str, Holdings]: ...

    async def contract_signature_ok(self, address: str, message: str, signature: bytes) -> bool: ...


@dataclass
class SyncReport:
    holdings: Holdings | None  # None: the chain could not be read this time
    wallets: int
    member: bool  # is the person in the Discord server
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)  # kit roles they wear now
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """E.g. "3 NFTs found - roles Holder, Collector given"."""
        if self.holdings is None:
            return "The blockchain could not be read right now; your roles will update on the next sync."
        n = self.holdings.total
        text = f"{n} NFT{'' if n == 1 else 's'} found"
        if not self.member:
            return text + " - join the Discord server and your roles will follow."
        if self.added:
            text += " - roles " + ", ".join(self.added) + " given"
        elif self.roles:
            text += " - roles " + ", ".join(self.roles)
        if self.removed:
            text += "; removed " + ", ".join(self.removed)
        if self.problems:
            text += " (but: " + "; ".join(self.problems) + ")"
        return text


@dataclass
class EnterResult:
    code: str  # see raffles.ENTRY_MESSAGES
    tickets: int = 0
    parts: list[str] = field(default_factory=list)
    wallet: dict[str, str] | None = None


class Kit:
    def __init__(self, settings: Settings, store: Store, reader: Reader, secret: bytes,
                 discord: DiscordPort | None = None, clock: Callable[[], float] = time.time,
                 rng: random.Random | None = None):
        self.s, self.store, self.reader, self.secret = settings, store, reader, secret
        self.discord, self.clock, self.rng = discord, clock, rng
        self.drawing: set[str] = set()  # raffles being drawn: no entries meanwhile
        self._sync_lock = asyncio.Lock()

    def now(self) -> int:
        return int(self.clock())

    def _touch(self, raffle_id: str) -> None:
        if self.discord:
            self.discord.raffle_changed(raffle_id)

    # links and wallets -----------------------------------------------------------------------------
    def verify_url(self, user_id: str, user_name: str) -> str:
        """The personal link the Verify button hands out (signed, 30 minutes)."""
        token = statetoken.sign(statetoken.VERIFY, {"u": str(user_id), "n": user_name[:64],
                                                    "g": str(self.s.discord.guild_id)},
                                self.secret, VERIFY_TTL, self.clock())
        return f"{self.s.project.public_url}/verify?state={token}"

    def link_wallet(self, user_id: str, user_name: str, kind: str, address: str) -> str | None:
        """Link a signed wallet; returns the Discord id it was moved away from, if any."""
        previous = self.store.link_wallet(kind, address, user_id, user_name, self.now())
        self.retarget(user_id, kind)
        if previous:
            self.retarget(previous, kind)
        return previous

    def unlink_wallet(self, user_id: str, kind: str, address: str) -> bool:
        done = self.store.unlink_wallet(kind, address, user_id)
        if done:
            self.retarget(user_id, kind)
        return done

    def raffle_wallet(self, user_id: str, kind: str) -> tuple[dict[str, str] | None, str]:
        """Where prizes of this wallet kind go, and why: ("set" by the person | "verified" | "")."""
        chosen = self.store.raffle_wallet(user_id, kind)
        if chosen:
            return chosen, "set"
        if kind in ("evm", "solana"):
            latest = next((w.address for w in self.store.wallets_of(user_id) if w.kind == kind), None)
            if latest:
                return {"address": latest}, "verified"
        return None, ""

    def parse_raffle_wallet(self, kind: str, values: Mapping[str, str]) -> tuple[dict[str, str] | None, str]:
        """Check typed raffle-wallet fields -> (wallet, error). All fields empty -> (None, ""): use the default."""
        values = {k: str(v or "").strip() for k, v in values.items()}
        if not any(values.values()):
            return None, ""
        if kind in ("evm", "solana"):
            address = values.get("address", "")
            if kind == "evm":
                ok = EVM_ADDRESS.match(address)
                address = address.lower()
            else:
                ok = SOLANA_ADDRESS.match(address)
            if not ok:
                return None, ("That does not look like an EVM address (0x and 40 hex characters)." if kind == "evm"
                              else "That does not look like a Solana address (base58, 32-44 characters).")
            return {"address": address}, ""
        out = {}
        for f in self.s.wallet_kinds[kind].fields:
            value = values.get(f.key, "")
            if not re.fullmatch(f.pattern, value):
                return None, f"{f.label}: that does not look right."
            out[f.key] = value
        return out, ""

    def set_raffle_wallet(self, user_id: str, kind: str, wallet: dict[str, str] | None) -> int:
        """Save (or clear) a raffle wallet; returns how many open entries now point at it."""
        self.store.set_raffle_wallet(user_id, kind, wallet, self.now())
        return self.retarget(user_id, kind)

    def retarget(self, user_id: str, kind: str) -> int:
        """Point the person's open entries of this wallet kind at their current raffle wallet."""
        wallet, _ = self.raffle_wallet(user_id, kind)
        if wallet is None:
            return 0
        moved = 0
        for rid in self.store.entered_raffles(user_id):
            r = self.store.raffle(rid)
            if not r or r.status != OPEN or r.wallet_kind != kind or rid in self.drawing:
                continue
            e = self.store.entry(rid, user_id)
            if e and e.wallet != wallet:
                self.store.put_entry(replace(e, wallet=wallet))
                moved += 1
        return moved

    # holdings and roles ----------------------------------------------------------------------------
    async def read(self, owners: Mapping[str, Sequence[tuple[str, str]]]) -> dict[str, Holdings]:
        """Fresh readings; each is also merged into the stored last reading (unknown parts keep old values)."""
        fresh = await self.reader.read(owners)
        for uid, h in fresh.items():
            prev = self.store.holdings(uid)
            if h.complete or h.specials_known or prev:
                self.store.save_holdings(uid, h.merged_with(prev))
        return fresh

    def last_holdings(self, user_id: str) -> Holdings | None:
        h = self.store.holdings(user_id)
        return h if h and h.complete else None

    def tickets_of(self, user_id: str) -> tuple[int, list[str]]:
        return ticket_count(self.last_holdings(user_id), self.s)

    async def sync_user(self, user_id: str, reason: str) -> SyncReport:
        """Read this person's wallets now and fix their roles."""
        owners = self.store.owners([user_id])
        fresh = (await self.read(owners))[user_id]
        return await self._apply(user_id, fresh, len(owners[user_id]), reason)

    async def sync_all(self) -> str:
        """Every linked member: one batched reading, then only the role changes that are needed."""
        if self._sync_lock.locked():
            return "skipped: the previous sync is still running"
        async with self._sync_lock:
            owners = self.store.owners()
            if not owners:
                return "no linked wallets yet"
            fresh = await self.read(owners)
            changed = unread = 0
            for uid, h in fresh.items():
                unread += not h.complete
                report = await self._apply(uid, h, len(owners[uid]), "holder sync")
                changed += bool(report.added or report.removed)
            return f"{len(owners)} linked, {changed} changed" + (f", {unread} not readable now" if unread else "")

    async def _apply(self, user_id: str, fresh: Holdings, wallets: int, reason: str) -> SyncReport:
        member = await self.discord.get_member(user_id) if self.discord else None
        report = SyncReport(fresh if fresh.complete else None, wallets, member is not None)
        if member is None:
            return report
        add, remove = role_changes(fresh, self.s, set(member.role_names))
        if add or remove:
            report.problems = await self.discord.edit_roles(user_id, add, remove, reason)
        have = {n.casefold() for n in member.role_names}
        report.added, report.removed = sorted(add), sorted(remove)
        report.roles = [r for r in self.s.holder_roles() if (r.casefold() in have or r in add) and r not in remove]
        return report

    async def auto_join(self, user_id: str, access_token: str) -> bool:
        """Add a verified person to the server (OAuth guilds.join) with the roles their holdings earn."""
        if not self.discord:
            return False
        h = self.last_holdings(user_id) or Holdings()
        return await self.discord.add_member(user_id, access_token, wanted_roles(h, self.s))

    # raffles -----------------------------------------------------------------------------------------
    def create_raffle(self, *, title: str, link: str, chain: str, duration: int, gtd: int, fcfs: int,
                      created_by: str, channel_id: str, eligible: Sequence[str], description: str = "",
                      image: str = "") -> Raffle:
        """Validate and store a new open raffle; ValueError carries a message for the person."""
        rc = self.s.chain(chain)
        if rc is None:
            raise ValueError(f"Unknown chain {chain!r}.")
        if duration < 60:
            raise ValueError("The duration looks wrong: try 24h, 2d 12h or 90m.")
        if gtd < 0 or fcfs < 0 or gtd + fcfs < 1:
            raise ValueError("Set at least one winner (gtd + fcfs).")
        if not re.match(r"^https?://\S+$", link.strip()):
            raise ValueError("The link must start with https://")
        if image.strip() and not re.match(r"^https://\S+$", image.strip()):
            raise ValueError("The image must be an https:// URL.")
        now = self.now()
        r = Raffle(id=new_raffle_id(self.store.raffle_ids()), title=title.strip()[:200], link=link.strip(),
                   chain=rc.name, wallet_kind=rc.wallet_kind, gtd=gtd, fcfs=fcfs, ends=now + duration, created=now,
                   created_by=created_by, channel_id=channel_id, eligible=list(eligible),
                   description=description.strip()[:1000], image=image.strip())
        self.store.save_raffle(r)
        return r

    def due_raffles(self) -> list[Raffle]:
        now = self.now()
        return [r for r in self.store.raffles(OPEN) if r.message_id and r.ends <= now and r.id not in self.drawing]

    async def enter(self, raffle_id: str, member: MemberInfo | None) -> EnterResult:
        """The one way into a raffle: the Enter button and the website both come here."""
        r = self.store.raffle(raffle_id)
        if r is None:
            return EnterResult("closed")
        code = refusal(r, self.clock(), raffle_id in self.drawing, member.role_ids if member else None)
        if code or member is None:
            return EnterResult(code or "not_member")
        uid = member.id
        linked = self.store.wallets_of(uid)
        h = None
        if linked:
            await self.read(self.store.owners([uid]))
            h = self.last_holdings(uid)
        tickets, parts = ticket_count(h, self.s)
        if self.s.raffles.require_holding:
            if not linked:
                return EnterResult("not_linked")
            if h is None:
                return EnterResult("rpc_busy")
            if tickets <= 0:
                return EnterResult("no_tokens")
        elif tickets <= 0:
            tickets, parts = 1, ["1 ticket for everyone"]
        wallet, _ = self.raffle_wallet(uid, r.wallet_kind)
        if wallet is None:
            return EnterResult("no_wallet")
        r = self.store.raffle(raffle_id)  # the chain read above awaited: check again, then write at once
        if r is None or r.status != OPEN or raffle_id in self.drawing or r.ends <= self.clock():
            return EnterResult("closing")
        before = self.store.entry(raffle_id, uid)
        self.store.put_entry(Entry(raffle_id, uid, member.name, wallet, tickets, before.at if before else self.now()))
        self._touch(raffle_id)
        return EnterResult("updated" if before else "entered", tickets, parts, wallet)

    async def draw(self, r: Raffle, exclude: Sequence[str] = (), n_gtd: int | None = None,
                   n_fcfs: int | None = None) -> tuple[list[str], list[str]]:
        """Recount every entrant on-chain in one batch (sold = out), then a weighted pick: GTD first, then FCFS."""
        entries = [e for e in self.store.entries(r.id) if e.user_id not in set(exclude)]
        await self.read(self.store.owners([e.user_id for e in entries]))
        pool = {}
        for e in entries:
            h = self.last_holdings(e.user_id)
            tickets = ticket_count(h, self.s)[0] if h else e.tickets  # never readable: what they entered with
            if not self.s.raffles.require_holding:
                tickets = max(tickets, 1)
            if tickets > 0:
                pool[e.user_id] = tickets
        n_gtd = r.gtd if n_gtd is None else n_gtd
        picked = weighted_pick(pool, n_gtd + (r.fcfs if n_fcfs is None else n_fcfs), self.rng)
        return picked[:n_gtd], picked[n_gtd:]

    def close_raffle(self, r: Raffle, gtd: list[str], fcfs: list[str], message_id: str = "",
                     channel_id: str = "") -> Raffle:
        wallets = {e.user_id: e.wallet for e in self.store.entries(r.id)}
        r.status = ENDED
        r.result = {"gtd": gtd, "fcfs": fcfs, "wallets": {u: wallets.get(u, {}) for u in gtd + fcfs},
                    "at": self.now(), "message": message_id, "channel": channel_id, "rerolls": []}
        self.store.save_raffle(r)
        return r

    def add_reroll(self, r: Raffle, winners: list[str]) -> Raffle:
        """Rerolled winners join the GTD list (like a fresh guaranteed spot)."""
        wallets = {e.user_id: e.wallet for e in self.store.entries(r.id)}
        r.result.setdefault("rerolls", []).append({"users": winners, "at": self.now()})
        r.result["gtd"] = list(r.result.get("gtd", [])) + winners
        r.result.setdefault("wallets", {}).update({u: wallets.get(u, {}) for u in winners})
        self.store.save_raffle(r)
        return r

    def cancel_raffle(self, r: Raffle) -> Raffle:
        r.status = CANCELLED
        self.store.save_raffle(r)
        return r

    def entry_text(self, r: Raffle | None, res: EnterResult, role_names: Sequence[str] = ()) -> str:
        """The person-facing reply for an entry result (same wording in Discord and on the website)."""
        return entry_message(res.code, roles=", ".join(role_names) or "the eligible roles", project=self.s.project.name,
                             chain=r.chain if r else "", kind=self.s.kind_label(r.wallet_kind) if r else "",
                             tickets=res.tickets, parts=", ".join(res.parts), wallet=wallet_text(res.wallet))

    def previous_winners(self, r: Raffle) -> list[str]:
        return list(r.result.get("gtd", [])) + list(r.result.get("fcfs", []))

    def find_raffles(self, text: str, limit: int = 25) -> list[Raffle]:
        """Raffles whose id or title contains `text` (for command autocomplete), newest first."""
        text = text.strip().lower()
        out = [r for r in self.store.raffles() if not text or text in r.id or text in r.title.lower()]
        return out[:limit]

    def wins_of(self, user_id: str) -> list[tuple[Raffle, str]]:
        out = []
        for r in self.store.raffles(ENDED):
            if user_id in r.result.get("gtd", []):
                out.append((r, "GTD"))
            elif user_id in r.result.get("fcfs", []):
                out.append((r, "FCFS"))
        return out
