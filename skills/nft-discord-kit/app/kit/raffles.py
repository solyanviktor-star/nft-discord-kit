"""Raffles (giveaways) without Discord: the data model, the rules, the draw and the texts.

The bot and the website both enter people through `kit.service.Kit.enter`, which applies
`refusal()` below, so the checks and the replies are the same everywhere.
"""
from __future__ import annotations

import csv
import io
import random
import re
import secrets
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

OPEN, ENDED, CANCELLED = "open", "ended", "cancelled"
CARD_YOUNG_GAP = 180  # a card younger than an hour is redrawn at most every 3 minutes
CARD_OLD_GAP = 900  # an older card at most every 15 minutes (Discord rate-limits edits of old messages)
DISCORD_EPOCH_MS = 1420070400000


@dataclass
class Raffle:
    id: str
    title: str
    link: str
    chain: str
    wallet_kind: str
    gtd: int
    fcfs: int
    ends: int
    created: int
    created_by: str
    channel_id: str
    eligible: list[str] = field(default_factory=list)  # role ids; empty = anyone in the server
    description: str = ""
    image: str = ""
    message_id: str = ""
    status: str = OPEN
    card_gone: str = ""  # the message id that answered 404: no more redraws for it
    result: dict[str, Any] = field(default_factory=dict)  # gtd, fcfs, wallets, at, message, rerolls

    @property
    def winners(self) -> int:
        return self.gtd + self.fcfs

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Raffle:
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Entry:
    raffle_id: str
    user_id: str
    user_name: str
    wallet: dict[str, str]  # {"address": ...} for evm/solana, the custom kind's fields otherwise
    tickets: int
    at: int


def new_raffle_id(taken: Iterable[str]) -> str:
    taken = set(taken)
    while True:
        rid = secrets.token_hex(3)
        if rid not in taken:
            return rid


_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60}
_DURATION = re.compile(r"(\d+)\s*(weeks?|w|days?|d|hours?|hrs?|h|minutes?|mins?|m)", re.I)


def parse_duration(text: str) -> int:
    """'24h', '2d 12h', '90m', '1w', '3 days' -> seconds; 0 when the text is not a duration."""
    text = (text or "").strip()
    if not text or _DURATION.sub("", text).strip(" ,"):
        return 0
    return sum(int(n) * _UNITS[unit[0].lower()] for n, unit in _DURATION.findall(text))


def weighted_pick(pool: Mapping[str, int], n: int, rng: random.Random | None = None) -> list[str]:
    """Up to n distinct winners; each pick's chance is proportional to tickets (integer weights)."""
    rng = rng or random.SystemRandom()
    left = {uid: int(w) for uid, w in pool.items() if int(w) > 0}
    out: list[str] = []
    while left and len(out) < n:
        x = rng.randrange(sum(left.values()))
        for uid, w in left.items():
            if x < w:
                out.append(uid)
                del left[uid]
                break
            x -= w
    return out


def refusal(r: Raffle, now: float, drawing: bool, member_role_ids: set[str] | frozenset[str] | None) -> str | None:
    """The first rule an entry breaks before holdings are read, or None."""
    if r.status != OPEN:
        return "closed"
    if drawing or r.ends <= now:
        return "closing"
    if member_role_ids is None:
        return "not_member"
    if r.eligible and not set(member_role_ids) & set(r.eligible):
        return "not_eligible"
    return None


ENTRY_MESSAGES = {
    "closed": "This giveaway has ended.",
    "closing": "This giveaway is being drawn right now.",
    "not_member": "Join the Discord server first, then enter.",
    "not_eligible": "This giveaway is for {roles} only. Verify your wallet to get your roles.",
    "not_linked": "Link your wallet first: press Verify in the verification channel. You only do it once.",
    "rpc_busy": "The blockchain node is busy. Try again in a minute.",
    "no_tokens": "No {project} NFTs found in your linked wallets. If they sit in another wallet, link that one too.",
    "no_wallet": "This giveaway is on {chain}: set your {kind} wallet first (Change Wallet).",
    "entered": "You're in! {tickets} ({parts}). Wallet: {wallet}",
    "updated": "You're already in: {tickets} ({parts}). Wallet: {wallet}",
}


def entry_message(code: str, **kw: Any) -> str:
    """The reply for an entry result code; unknown placeholders stay empty."""
    tickets = kw.get("tickets")
    if isinstance(tickets, int):
        kw["tickets"] = f"{tickets} ticket{'' if tickets == 1 else 's'}"
    return ENTRY_MESSAGES.get(code, "Something went wrong.").format_map(_Blank(kw))


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return ""


def snowflake_time(snowflake: str | int) -> float:
    """Unix seconds a Discord id was created at; 0 for a bad id."""
    try:
        return ((int(snowflake) >> 22) + DISCORD_EPOCH_MS) / 1000
    except (TypeError, ValueError):
        return 0.0


def redraw_due(now: float, last_redraw: float, card_created: float) -> bool:
    """May a card be edited again? Young cards every 3 minutes, cards older than an hour every 15."""
    gap = CARD_YOUNG_GAP if now - card_created < 3600 else CARD_OLD_GAP
    return now - last_redraw >= gap


def mention_chunks(user_ids: Sequence[str], limit: int = 1900) -> list[str]:
    """User mentions packed into messages under Discord's 2000-character limit."""
    chunks, cur = [], ""
    for uid in user_ids:
        m = f"<@{uid}>"
        if cur and len(cur) + 1 + len(m) > limit:
            chunks.append(cur)
            cur = m
        else:
            cur = f"{cur} {m}" if cur else m
    return chunks + [cur] if cur else chunks


def wallet_text(wallet: Mapping[str, str] | None) -> str:
    if not wallet:
        return "not set"
    if set(wallet) == {"address"}:
        return wallet["address"]
    return ", ".join(f"{k}: {v}" for k, v in wallet.items())


def card_embed(r: Raffle, entrants: int, eligible_mentions: Sequence[str], project: str, color: int,
               rule: str) -> dict[str, Any]:
    """The raffle card as a Discord embed dict."""
    prefix = {ENDED: "[ENDED] ", CANCELLED: "[CANCELLED] "}.get(r.status, "")
    e: dict[str, Any] = {
        "title": (prefix + r.title)[:256],
        "description": r.link + (f"\n\n{r.description}" if r.description else ""),
        "color": color if r.status == OPEN else 0x555555,
        "fields": [
            {"name": "Ends", "value": f"<t:{r.ends}:f>\n<t:{r.ends}:R>", "inline": True},
            {"name": "Chain", "value": r.chain, "inline": True},
            {"name": "Winners", "value": str(r.winners), "inline": True},
            {"name": "Entrants", "value": str(entrants), "inline": True},
            {"name": "Guaranteed", "value": str(r.gtd), "inline": True},
            {"name": "FCFS", "value": str(r.fcfs), "inline": True},
            {"name": "Eligible Roles", "value": " ".join(eligible_mentions)[:1024] or "Everyone in the server",
             "inline": False},
            {"name": "Tickets", "value": rule[:1024], "inline": False},
        ],
        "footer": {"text": f"{project} Giveaways · {r.id}"},
    }
    if r.image:
        e["image"] = {"url": r.image}
    return e


def winners_embed(r: Raffle, gtd: Sequence[str], fcfs: Sequence[str], project: str,
                  reroll: bool = False) -> dict[str, Any]:
    """The winners announcement; with a huge list the embed shows counts (the mentions go above it)."""
    mention = " ".join(f"<@{u}>" for u in gtd), " ".join(f"<@{u}>" for u in fcfs)
    lines = [f"**GTD:** {mention[0]}", f"**FCFS:** {mention[1]}"] if gtd and fcfs else [
        mention[0] or mention[1] or "No eligible entrants."]
    note = "Congratulations! Prizes go to the wallet you entered with. The team will post the next steps."
    desc = "\n".join(lines) + "\n\n" + note
    if len(desc) > 4000:
        desc = f"**GTD:** {len(gtd)} · **FCFS:** {len(fcfs)} winners, tagged above.\n\n{note}"
    title = ("WINNERS: REROLL · " if reroll else "WINNERS: ") + r.title
    return {"title": title[:256], "description": desc,
            "color": 0x2ECC71, "fields": [{"name": "Details", "value": r.link[:1024], "inline": False}],
            "footer": {"text": f"{project} Giveaways · {r.id}"}}


def wallet_columns(kind: str, field_keys: Sequence[str] = ()) -> list[tuple[str, str]]:
    """(CSV column, wallet key) pairs: one `wallet` column for evm/solana, one per field for custom kinds."""
    if kind in ("evm", "solana"):
        return [("wallet", "address")]
    return [(f"wallet_{k}", k) for k in field_keys]


def export_csv(r: Raffle, entries: Sequence[Entry], columns: Sequence[tuple[str, str]], winners: bool) -> str:
    """Winners (type, id, name, wallet columns) or every entrant (id, name, wallet columns, tickets)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    by_user = {e.user_id: e for e in entries}
    if winners:
        w.writerow(["type", "discord_id", "discord_name"] + [c for c, _ in columns])
        wallets = r.result.get("wallets") or {}
        for kind in ("gtd", "fcfs"):
            for uid in r.result.get(kind) or []:
                e = by_user.get(uid)
                wallet = wallets.get(uid) or (e.wallet if e else {})
                w.writerow([kind.upper(), uid, e.user_name if e else ""] + [wallet.get(k, "") for _, k in columns])
    else:
        w.writerow(["discord_id", "discord_name"] + [c for c, _ in columns] + ["tickets"])
        for e in entries:
            w.writerow([e.user_id, e.user_name] + [e.wallet.get(k, "") for _, k in columns] + [e.tickets])
    return buf.getvalue()
